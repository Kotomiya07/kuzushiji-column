"""
くずし字列分割アノテーションツール - FastAPI Backend
"""

from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from pydantic import BaseModel

# ===== 設定 =====
RAW_DIR = Path("raw")
OUTPUT_DIR = Path("output")

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
    """書籍一覧を取得"""
    books = get_book_dirs()
    return {"books": books}


@app.get("/api/books/{book_id}/pages")
async def get_pages(book_id: str):
    """ページ一覧を取得"""
    pages = get_page_ids(book_id)
    if not pages:
        raise HTTPException(status_code=404, detail=f"Book not found: {book_id}")
    
    # 列アノテーション済みのページを取得
    annotated_pages = []
    output_csv = OUTPUT_DIR / book_id / "column_annotation.csv"
    if output_csv.exists():
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


@app.post("/api/books/{book_id}/pages/{page_id}/save")
async def save_annotations(book_id: str, page_id: str, request: SaveRequest):
    """アノテーションを保存し、列画像を生成"""
    # 出力ディレクトリ作成
    output_book_dir = OUTPUT_DIR / book_id
    columns_dir = output_book_dir / "columns"
    columns_dir.mkdir(parents=True, exist_ok=True)

    # 元の座標CSVを読み込み
    df = load_coordinate_csv(book_id)
    # page_idはImage列の値そのもの
    image_name = page_id

    # Column IDを追加（既存のColumn ID列があれば更新）
    if "Column ID" not in df.columns:
        df["Column ID"] = None

    # 現在のページのColumn IDをクリア
    df.loc[df["Image"] == image_name, "Column ID"] = None

    # 列ごとにColumn IDを設定
    for col_idx, column in enumerate(request.columns, start=1):
        column_id = f"COL{col_idx:04d}"
        for char_id in column.char_ids:
            mask = (df["Image"] == image_name) & (df["Char ID"] == char_id)
            df.loc[mask, "Column ID"] = column_id

    # CSVを保存
    output_csv = output_book_dir / "column_annotation.csv"

    # 既存のCSVがあれば、他のページのデータを保持
    if output_csv.exists():
        existing_df = pd.read_csv(output_csv)
        # 現在のページ以外のデータを保持
        other_pages_df = existing_df[existing_df["Image"] != image_name]
        # 現在のページのデータ
        current_page_df = df[df["Image"] == image_name]
        # マージ
        final_df = pd.concat([other_pages_df, current_page_df], ignore_index=True)
    else:
        final_df = df[df["Image"] == image_name]

    final_df.to_csv(output_csv, index=False)

    # 列画像を生成
    image_path = get_image_path(book_id, page_id)
    padding = 10  # 周囲に追加するパディング（ピクセル）
    
    # 既存のこのページの列画像を削除
    for old_file in columns_dir.glob(f"{page_id}_col*.jpg"):
        old_file.unlink()
    
    with Image.open(image_path) as img:
        img_width, img_height = img.size
        page_df = df[df["Image"] == image_name]

        for col_idx, column in enumerate(request.columns, start=1):
            # 列に含まれる文字のbboxを取得
            col_chars = page_df[page_df["Char ID"].isin(column.char_ids)]
            if col_chars.empty:
                continue

            # 列全体のbboxを計算
            min_x = col_chars["X"].min()
            min_y = col_chars["Y"].min()
            max_x = (col_chars["X"] + col_chars["Width"]).max()
            max_y = (col_chars["Y"] + col_chars["Height"]).max()

            # パディングを追加（画像範囲内に収める）
            min_x = max(0, min_x - padding)
            min_y = max(0, min_y - padding)
            max_x = min(img_width, max_x + padding)
            max_y = min(img_height, max_y + padding)

            # 画像を切り出し
            column_img = img.crop((min_x, min_y, max_x, max_y))
            column_path = columns_dir / f"{page_id}_col{col_idx:04d}.jpg"
            column_img.save(column_path, "JPEG", quality=95)

    return {
        "success": True,
        "message": f"保存完了: {len(request.columns)}列",
        "csv_path": str(output_csv),
        "columns_dir": str(columns_dir),
    }


# ===== メイン =====
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
