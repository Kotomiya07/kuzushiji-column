"""
くずし字列分割アノテーションツール - FastAPI Backend
"""

from pathlib import Path
from dataclasses import dataclass
from typing import Optional, cast

import csv
import re

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from pydantic import BaseModel

# ===== 設定 =====
RAW_DIR = Path("raw")
OUTPUT_DIR = Path("output")
OUTPUT_SEG_DIR = Path("output_seg")
INDEX_FILENAME = "column_annotation.index"

app = FastAPI(title="くずし字列分割アノテーションツール")

# 静的ファイルとテンプレート
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ===== Pydantic モデル =====
class CharInfo(BaseModel):
    """文字情報"""

    unicode: str
    image: str
    x: int
    y: int
    block_id: str
    char_id: str
    width: int
    height: int
    column_id: Optional[str] = None
    segment_id: Optional[str] = None


class PageData(BaseModel):
    """ページデータ"""

    book_id: str
    page_id: str
    image_width: int
    image_height: int
    characters: list[CharInfo]


class ColumnData(BaseModel):
    """列データ（保存用）"""

    char_ids: list[str]


class SaveRequest(BaseModel):
    """保存リクエスト"""

    book_id: str
    page_id: str
    columns: list[ColumnData]


# ===== ヘルパー関数 =====
def get_book_dirs() -> list[str]:
    """書籍ディレクトリ一覧を取得"""
    if not RAW_DIR.exists():
        return []
    return sorted(
        [
            d.name
            for d in RAW_DIR.iterdir()
            if d.is_dir() and not d.name.startswith(".") and not d.name.endswith(".md")
        ]
    )


def get_page_ids(book_id: str) -> list[str]:
    """指定書籍のページID一覧を取得（CSVにアノテーションが存在するページのみ）"""
    # CSVを読み込んでアノテーションが存在するページを取得
    csv_path = RAW_DIR / book_id / f"{book_id}_coordinate.csv"
    if not csv_path.exists():
        return []

    df = pd.read_csv(csv_path)
    
    # 画像ディレクトリを確認
    images_dir = RAW_DIR / book_id / "images"
    if not images_dir.exists():
        return []

    # Image列からユニークな画像名を取得し、実際に存在するものだけをページとして返す
    pages = []
    for image_name in df["Image"].unique():
        image_path = images_dir / f"{image_name}.jpg"
        if image_path.exists():
            pages.append(image_name)

    return sorted(pages)


def load_coordinate_csv(book_id: str) -> pd.DataFrame:
    """座標CSVを読み込み"""
    csv_path = RAW_DIR / book_id / f"{book_id}_coordinate.csv"
    if not csv_path.exists():
        raise HTTPException(status_code=404, detail=f"CSV not found: {csv_path}")
    return pd.read_csv(csv_path)


def load_existing_annotations(book_id: str, page_id: str) -> dict[str, str]:
    """既存のアノテーションを読み込み（Column IDのマッピング）"""
    output_csv = OUTPUT_DIR / book_id / "column_annotation.csv"
    if not output_csv.exists():
        return {}

    df = pd.read_csv(output_csv)
    # page_idはImage列の値そのもの
    page_df = df[df["Image"] == page_id]

    result = {}
    for _, row in page_df.iterrows():
        if pd.notna(row.get("Column ID")):
            result[row["Char ID"]] = row["Column ID"]
    return result


def load_existing_segment_annotations(book_id: str, page_id: str) -> dict[str, str]:
    """既存のセグメントアノテーションを読み込み（Segment IDのマッピング）"""
    seg_csv = OUTPUT_SEG_DIR / book_id / "column_annotation.csv"
    if not seg_csv.exists():
        return {}

    df = pd.read_csv(seg_csv)
    page_df = df[df["Image"] == page_id]

    result: dict[str, str] = {}
    for _, row in page_df.iterrows():
        seg_id = row.get("Segment ID")
        if pd.isna(seg_id):
            continue
        seg_str = str(seg_id).strip()
        if not seg_str:
            continue

        char_id = row.get("Char ID")
        if pd.isna(char_id):
            continue
        result[str(char_id)] = seg_str

    return result


def load_existing_segment_map(book_id: str, page_id: str) -> dict[str, str]:
    """既存のセグメント割当を読み込み（Column ID -> Segment ID）"""
    seg_csv = OUTPUT_SEG_DIR / book_id / "column_annotation.csv"
    if not seg_csv.exists():
        return {}

    df = pd.read_csv(seg_csv)
    page_df = df[df["Image"] == page_id]
    if page_df.empty:
        return {}

    result: dict[str, str] = {}
    for _, row in page_df.iterrows():
        col_id = row.get("Column ID")
        seg_id = row.get("Segment ID")
        if pd.isna(col_id) or pd.isna(seg_id):
            continue
        col_str = str(col_id).strip()
        seg_str = str(seg_id).strip()
        if not col_str or not seg_str:
            continue
        result[col_str] = seg_str

    return result


def load_page_index(book_id: str) -> set[str]:
    """アノテーション済みページのインデックスを読み込み（なければ生成）"""
    output_book_dir = OUTPUT_DIR / book_id
    index_path = output_book_dir / INDEX_FILENAME
    output_csv = output_book_dir / "column_annotation.csv"

    if index_path.exists():
        return {line.strip() for line in index_path.read_text().splitlines() if line.strip()}

    if not output_csv.exists():
        return set()

    pages = set()
    with output_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            image_name = row.get("Image")
            if image_name:
                pages.add(image_name)

    if pages:
        output_book_dir.mkdir(parents=True, exist_ok=True)
        index_path.write_text("\n".join(sorted(pages)) + "\n", encoding="utf-8")

    return pages


def get_image_path(book_id: str, page_id: str) -> Path:
    """画像パスを取得（page_idはImage列の値）"""
    return RAW_DIR / book_id / "images" / f"{page_id}.jpg"


# ===== API エンドポイント =====
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """メインページ"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/books")
async def get_books():
    """書籍一覧を取得（進捗率付き）"""
    books = get_book_dirs()
    
    result = []
    for book_id in books:
        # 総ページ数を取得
        pages = get_page_ids(book_id)
        total_pages = len(pages)
        
        # アノテーション済みページ数を取得
        annotated_count = 0
        output_csv = OUTPUT_DIR / book_id / "column_annotation.csv"
        index_path = OUTPUT_DIR / book_id / INDEX_FILENAME
        if index_path.exists():
            annotated_images = {
                line.strip() for line in index_path.read_text().splitlines() if line.strip()
            }
            for img_name in annotated_images:
                if img_name in pages:
                    annotated_count += 1
        elif output_csv.exists():
            ann_df = pd.read_csv(output_csv)
            if "Column ID" in ann_df.columns:
                annotated_images = ann_df[ann_df["Column ID"].notna()]["Image"].unique()
                for img_name in annotated_images:
                    if img_name in pages:
                        annotated_count += 1
        
        # 進捗率を計算
        progress = round(annotated_count / total_pages * 100) if total_pages > 0 else 0
        
        result.append({
            "book_id": book_id,
            "total_pages": total_pages,
            "annotated_count": annotated_count,
            "progress": progress,
        })
    
    return {"books": result}


@app.get("/api/books/{book_id}/pages")
async def get_pages(book_id: str):
    """ページ一覧を取得"""
    pages = get_page_ids(book_id)
    if not pages:
        raise HTTPException(status_code=404, detail=f"Book not found: {book_id}")
    
    # 列アノテーション済みのページを取得
    annotated_pages = []
    output_csv = OUTPUT_DIR / book_id / "column_annotation.csv"
    index_path = OUTPUT_DIR / book_id / INDEX_FILENAME
    if index_path.exists():
        annotated_images = [
            line.strip() for line in index_path.read_text().splitlines() if line.strip()
        ]
        for img_name in annotated_images:
            if img_name in pages:
                annotated_pages.append(img_name)
    elif output_csv.exists():
        ann_df = pd.read_csv(output_csv)
        if "Column ID" in ann_df.columns:
            # Column IDが設定されているページを取得
            # page_idはImage列の値そのもの
            annotated_images = ann_df[ann_df["Column ID"].notna()]["Image"].unique()
            for img_name in annotated_images:
                if img_name in pages:
                    annotated_pages.append(img_name)
    
    # ソートして最後のアノテーション済みページを特定
    annotated_pages = sorted(annotated_pages)
    last_annotated = annotated_pages[-1] if annotated_pages else None
    
    return {
        "pages": pages,
        "annotated_pages": annotated_pages,
        "last_annotated": last_annotated,
    }


@app.get("/api/books/{book_id}/pages/{page_id}")
async def get_page_data(book_id: str, page_id: str):
    """ページデータ（文字座標含む）を取得"""
    # 画像サイズを取得
    image_path = get_image_path(book_id, page_id)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image not found: {image_path}")

    with Image.open(image_path) as img:
        image_width, image_height = img.size

    # 座標データを読み込み
    df = load_coordinate_csv(book_id)
    # page_idはImage列の値そのもの
    page_df = df[df["Image"] == page_id].copy()

    # Char IDでソート（C0001, C0002, ...）
    page_df["char_id_num"] = page_df["Char ID"].str.extract(r"C(\d+)").astype(int)
    page_df = page_df.sort_values("char_id_num")

    # 既存のアノテーションを読み込み
    existing_annotations = load_existing_annotations(book_id, page_id)
    existing_segment_annotations = load_existing_segment_annotations(book_id, page_id)

    characters = []
    for _, row in page_df.iterrows():
        char_id = row["Char ID"]
        # Block IDがNaNの場合は空文字列にする
        block_id = row["Block ID"]
        if pd.isna(block_id):
            block_id = ""
        characters.append(
            CharInfo(
                unicode=str(row["Unicode"]),
                image=str(row["Image"]),
                x=int(row["X"]),
                y=int(row["Y"]),
                block_id=str(block_id),
                char_id=str(char_id),
                width=int(row["Width"]),
                height=int(row["Height"]),
                column_id=existing_annotations.get(char_id),
                segment_id=existing_segment_annotations.get(char_id),
            )
        )

    return PageData(
        book_id=book_id,
        page_id=page_id,
        image_width=image_width,
        image_height=image_height,
        characters=characters,
    )


@app.get("/api/books/{book_id}/pages/{page_id}/image")
async def get_page_image(book_id: str, page_id: str):
    """ページ画像を返す"""
    image_path = get_image_path(book_id, page_id)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image not found: {image_path}")
    return FileResponse(image_path, media_type="image/jpeg")




@dataclass(frozen=True)
class ColumnBox:
    column_id: str
    left: int
    right: int
    top: int
    bottom: int
    cx: float
    cw: int
    ch: int


def _median_int(values: list[int]) -> int:
    if not values:
        return 0
    sorted_vals = sorted(values)
    mid = len(sorted_vals) // 2
    if len(sorted_vals) % 2 == 1:
        return int(sorted_vals[mid])
    return int((sorted_vals[mid - 1] + sorted_vals[mid]) / 2)


def _compute_column_boxes(page_df: pd.DataFrame) -> list[ColumnBox]:
    if "Column ID" not in page_df.columns:
        return []

    boxes: list[ColumnBox] = []
    grouped = page_df.dropna(subset=["Column ID"]).groupby("Column ID", sort=True)
    for col_id, g in grouped:
        left = int(g["X"].min())
        top = int(g["Y"].min())
        right = int((g["X"] + g["Width"]).max())
        bottom = int((g["Y"] + g["Height"]).max())
        cw = max(1, right - left)
        ch = max(1, bottom - top)
        cx = (left + right) / 2
        boxes.append(
            ColumnBox(
                column_id=str(col_id),
                left=left,
                right=right,
                top=top,
                bottom=bottom,
                cx=float(cx),
                cw=cw,
                ch=ch,
            )
        )

    boxes.sort(key=lambda b: b.column_id)
    return boxes


def _estimate_segments_for_page(page_df: pd.DataFrame) -> dict[str, str]:
    boxes = _compute_column_boxes(page_df)
    if not boxes:
        return {}

    def x_overlap_ratio(a: ColumnBox, b: ColumnBox) -> float:
        x_inter = max(0, min(a.right, b.right) - max(a.left, b.left))
        min_w = max(1, min(a.cw, b.cw))
        return x_inter / min_w

    def y_overlap_ratio(a: ColumnBox, b: ColumnBox) -> float:
        y_inter = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))
        min_h = max(1, min(a.ch, b.ch))
        return y_inter / min_h

    def rect_for_cols(col_ids: list[str]) -> tuple[int, int, int, int]:
        left = min(box.left for box in boxes if box.column_id in col_ids)
        top = min(box.top for box in boxes if box.column_id in col_ids)
        right = max(box.right for box in boxes if box.column_id in col_ids)
        bottom = max(box.bottom for box in boxes if box.column_id in col_ids)
        return (left, top, right, bottom)

    def overlap_ratio(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
        al, at, ar, ab = a
        bl, bt, br, bb = b
        inter_w = max(0, min(ar, br) - max(al, bl))
        inter_h = max(0, min(ab, bb) - max(at, bt))
        inter = inter_w * inter_h
        if inter <= 0:
            return 0.0
        area_a = max(1, (ar - al) * (ab - at))
        area_b = max(1, (br - bl) * (bb - bt))
        return inter / min(area_a, area_b)

    rect_overlap_th_closed = 0.1
    rect_overlap_th_future = 0.05
    x_th = 0.6
    stacked_x_th = 0.35
    stacked_y_th = 0.1

    by_id = {box.column_id: box for box in boxes}

    segments: list[list[str]] = []
    closed_rects: list[tuple[int, int, int, int]] = []

    current: list[str] = [boxes[0].column_id]
    current_rect = rect_for_cols(current)

    for i in range(1, len(boxes)):
        cur = boxes[i]
        cand_cols = current + [cur.column_id]
        cand_rect = rect_for_cols(cand_cols)

        if any(overlap_ratio(cand_rect, r) > rect_overlap_th_closed for r in closed_rects):
            segments.append(current)
            closed_rects.append(current_rect)
            current = [cur.column_id]
            current_rect = rect_for_cols(current)
            continue

        if any(
            (x_overlap_ratio(box, cur) >= x_th)
            or (
                (x_overlap_ratio(box, cur) >= stacked_x_th)
                and (y_overlap_ratio(box, cur) <= stacked_y_th)
            )
            for box in boxes
            if box.column_id in current
        ):
            segments.append(current)
            closed_rects.append(current_rect)
            current = [cur.column_id]
            current_rect = rect_for_cols(current)
            continue

        cand_boxes = [by_id[cid] for cid in cand_cols]

        intersects_future = False
        for fut in boxes[i + 1 :]:
            if not any(x_overlap_ratio(b, fut) >= x_th for b in cand_boxes):
                continue
            fut_rect = (fut.left, fut.top, fut.right, fut.bottom)
            if overlap_ratio(cand_rect, fut_rect) > rect_overlap_th_future:
                intersects_future = True
                break

        if intersects_future:
            segments.append(current)
            closed_rects.append(current_rect)
            current = [cur.column_id]
            current_rect = rect_for_cols(current)
            continue

        current = cand_cols
        current_rect = cand_rect

    segments.append(current)

    mapping: dict[str, str] = {}
    for seg_idx, cols_in_seg in enumerate(segments, start=1):
        seg_id = f"SEG{seg_idx:04d}"
        for col_id in cols_in_seg:
            mapping[col_id] = seg_id

    return mapping



def _update_output_seg_csv(
    book_id: str,
    page_id: str,
    page_df: pd.DataFrame,
    segment_map_override: Optional[dict[str, str]] = None,
) -> None:
    out_dir = OUTPUT_SEG_DIR / book_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "column_annotation.csv"

    seg_df = page_df.copy()
    if "Column ID" not in seg_df.columns:
        seg_df["Column ID"] = None

    seg_map = _estimate_segments_for_page(seg_df)

    def to_seg(val: object) -> str:
        if pd.isna(val):
            return ""
        col_id = str(val)
        if segment_map_override is not None:
            override = segment_map_override.get(col_id)
            if override is not None:
                override_str = str(override).strip()
                if override_str:
                    return override_str
        return seg_map.get(col_id, "")

    seg_df["Segment ID"] = seg_df["Column ID"].map(to_seg)

    if not out_csv.exists():
        seg_df.to_csv(out_csv, index=False)
        return

    tmp_path = out_csv.with_suffix(".tmp")
    with out_csv.open("r", encoding="utf-8", newline="") as src, tmp_path.open(
        "w", encoding="utf-8", newline=""
    ) as dst:
        reader = csv.DictReader(src)
        fieldnames = list(reader.fieldnames or [])
        for col in seg_df.columns:
            if col not in fieldnames:
                fieldnames.append(col)
        if "Segment ID" not in fieldnames:
            fieldnames.append("Segment ID")

        writer = csv.DictWriter(dst, fieldnames=fieldnames)
        writer.writeheader()
        for row in reader:
            if row.get("Image") == page_id:
                continue
            writer.writerow({name: row.get(name, "") for name in fieldnames})

        for record in seg_df.to_dict(orient="records"):
            writer.writerow({name: record.get(name, "") for name in fieldnames})

    tmp_path.replace(out_csv)


def generate_column_images(
    book_id: str,
    page_id: str,
    columns: list[list[str]],
) -> None:
    columns_dir = OUTPUT_DIR / book_id / "columns"
    columns_dir.mkdir(parents=True, exist_ok=True)

    image_path = get_image_path(book_id, page_id)
    if not image_path.exists():
        return

    df = load_coordinate_csv(book_id)
    page_df = df[df["Image"] == page_id]
    if page_df.empty:
        return

    padding = 10

    for old_file in columns_dir.glob(f"{page_id}_col*.jpg"):
        old_file.unlink()

    with Image.open(image_path) as img:
        img_width, img_height = img.size

        for col_idx, char_ids in enumerate(columns, start=1):
            col_chars = page_df[page_df["Char ID"].isin(char_ids)]
            if col_chars.empty:
                continue

            min_x = int(col_chars["X"].min())
            min_y = int(col_chars["Y"].min())
            max_x = int((col_chars["X"] + col_chars["Width"]).max())
            max_y = int((col_chars["Y"] + col_chars["Height"]).max())

            min_x = max(0, min_x - padding)
            min_y = max(0, min_y - padding)
            max_x = min(img_width, max_x + padding)
            max_y = min(img_height, max_y + padding)

            column_img = img.crop((min_x, min_y, max_x, max_y))
            column_path = columns_dir / f"{page_id}_col{col_idx:04d}.jpg"
            column_img.save(column_path, "JPEG", quality=95)


@app.post("/api/books/{book_id}/pages/{page_id}/save")
async def save_annotations(
    book_id: str,
    page_id: str,
    request: SaveRequest,
    background_tasks: BackgroundTasks,
):
    """アノテーションを保存し、列画像を生成"""
    # 出力ディレクトリ作成
    output_book_dir = OUTPUT_DIR / book_id
    columns_dir = output_book_dir / "columns"
    columns_dir.mkdir(parents=True, exist_ok=True)

    # 元の座標CSVを読み込み
    df = load_coordinate_csv(book_id)
    # page_idはImage列の値そのもの
    image_name = page_id

    # 現在のページだけに絞って処理
    page_df = cast(pd.DataFrame, df[df["Image"] == image_name].copy())

    # Column IDを追加（既存のColumn ID列があれば更新）
    if "Column ID" not in page_df.columns:
        page_df["Column ID"] = None

    # 現在のページのColumn IDをクリア
    page_df.loc[:, "Column ID"] = None

    # 列ごとにColumn IDを設定
    for col_idx, column in enumerate(request.columns, start=1):
        column_id = f"COL{col_idx:04d}"
        for char_id in column.char_ids:
            mask = page_df["Char ID"] == char_id
            page_df.loc[mask, "Column ID"] = column_id

    # CSVを保存
    output_csv = output_book_dir / "column_annotation.csv"

    index_path = output_book_dir / INDEX_FILENAME
    page_index = load_page_index(book_id)

    if image_name not in page_index:
        if not output_csv.exists():
            page_df.to_csv(output_csv, index=False)
        else:
            # 新規ページなら追記のみ
            page_df.to_csv(output_csv, mode="a", index=False, header=False)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("a", encoding="utf-8") as f:
            f.write(f"{image_name}\n")
        page_index.add(image_name)
    else:
        # 既存ページは該当行を除外して書き換え
        tmp_path = output_csv.with_suffix(".tmp")
        with output_csv.open("r", encoding="utf-8", newline="") as src, tmp_path.open(
            "w", encoding="utf-8", newline=""
        ) as dst:
            reader = csv.DictReader(src)
            fieldnames = list(reader.fieldnames or [])
            for col in page_df.columns:
                if col not in fieldnames:
                    fieldnames.append(col)
            if "Column ID" not in fieldnames:
                fieldnames.append("Column ID")

            writer = csv.DictWriter(dst, fieldnames=fieldnames)
            writer.writeheader()
            for row in reader:
                if row.get("Image") == image_name:
                    continue
                writer.writerow({name: row.get(name, "") for name in fieldnames})

        page_df.to_csv(tmp_path, mode="a", index=False, header=False)
        tmp_path.replace(output_csv)

    # 列画像はバックグラウンドで生成
    columns_char_ids = [column.char_ids for column in request.columns]
    background_tasks.add_task(generate_column_images, book_id, page_id, columns_char_ids)

    # 既存のセグメント（手動編集）を可能な限り保持
    segment_override = load_existing_segment_map(book_id, page_id)
    _update_output_seg_csv(book_id, page_id, page_df, segment_map_override=segment_override or None)

    return {
        "success": True,
        "message": f"保存完了: {len(request.columns)}列（列画像はバックグラウンド生成）",
        "csv_path": str(output_csv),
        "columns_dir": str(columns_dir),
    }


class SegmentData(BaseModel):
    """セグメントデータ（保存用）"""

    segment_id: str
    column_ids: list[str]


class SaveSegmentsRequest(BaseModel):
    """セグメント保存リクエスト"""

    book_id: str
    page_id: str
    segments: list[SegmentData]


@app.post("/api/books/{book_id}/pages/{page_id}/save_segments")
async def save_segments(book_id: str, page_id: str, request: SaveSegmentsRequest):
    """セグメント（Segment ID）を保存"""
    if request.book_id != book_id or request.page_id != page_id:
        raise HTTPException(status_code=400, detail="book_id/page_id mismatch")

    output_csv = OUTPUT_DIR / book_id / "column_annotation.csv"
    if not output_csv.exists():
        raise HTTPException(
            status_code=400,
            detail="column annotations not found. Save columns first.",
        )

    # Column ID -> Segment ID のマップを作成（簡易バリデーション）
    seg_map: dict[str, str] = {}
    seg_id_re = re.compile(r"^SEG\d{4}$")
    for seg in request.segments:
        seg_id = str(seg.segment_id).strip()
        if not seg_id_re.match(seg_id):
            raise HTTPException(status_code=400, detail=f"invalid Segment ID: {seg_id}")
        for col_id in seg.column_ids:
            col = str(col_id).strip()
            if not col:
                continue
            seg_map[col] = seg_id

    # output/{book_id}/column_annotation.csv を読み込み、該当ページを更新して output_seg に書き出す
    df = pd.read_csv(output_csv)
    page_df = cast(pd.DataFrame, df[df["Image"] == page_id].copy())
    if page_df.empty:
        raise HTTPException(status_code=404, detail=f"page not found in annotations: {page_id}")

    if "Column ID" not in page_df.columns:
        raise HTTPException(status_code=400, detail="Column ID column not found")

    page_cols = {str(v).strip() for v in page_df["Column ID"].dropna().unique().tolist() if str(v).strip()}
    if not page_cols:
        raise HTTPException(status_code=400, detail="no Column ID found for this page")

    provided_cols = set(seg_map.keys())
    unknown = sorted(provided_cols - page_cols)
    missing = sorted(page_cols - provided_cols)
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown Column ID(s): {', '.join(unknown)}")
    if missing:
        raise HTTPException(status_code=400, detail=f"missing Column ID(s): {', '.join(missing)}")

    _update_output_seg_csv(book_id, page_id, page_df, segment_map_override=seg_map)

    out_csv = OUTPUT_SEG_DIR / book_id / "column_annotation.csv"
    return {
        "success": True,
        "message": f"セグメント保存完了: {len(request.segments)}件",
        "csv_path": str(out_csv),
    }


# ===== メイン =====
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8100, reload=True)
