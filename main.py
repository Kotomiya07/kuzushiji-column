"""
くずし字列分割アノテーションツール - FastAPI Backend
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional, cast

import csv
import json
import math
import os
import re
import subprocess
import threading
import uuid

import pandas as pd
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from pydantic import BaseModel

# ===== 設定 =====
RAW_DIR = Path("raw")
OUTPUT_DIR = Path("output")
OUTPUT_SEG_DIR = Path("output_seg")
OUTPUT_MODEL_DIR = Path("output_models")
IMAGE_CACHE_DIR = Path(".cache") / "page_images"
PROJECT_ROOT = Path(__file__).resolve().parent
APP_HOST = "127.0.0.1"
APP_PORT = 8100
APP_RELOAD = True
APP_RELOAD_DIRS = [str(Path(__file__).resolve().parent)]
APP_RELOAD_EXCLUDES = [
    ".venv/*",
    ".cache/*",
    "raw/*",
    "output/*",
    "output_seg/*",
    "output_models/*",
]
INDEX_FILENAME = "column_annotation.index"
MODEL_VERSION = 1
MIN_MODEL_SAMPLES = 8
TRAINING_HISTORY_FILENAME = "training_history.jsonl"
FEEDBACK_HISTORY_FILENAME = "feedback_history.jsonl"
HISTORY_PREVIEW_LIMIT = 10
GLOBAL_MODEL_BOOK_ID = "global"
TRAINING_SAMPLE_CACHE_FILENAME = "training_samples.json"
HF_CONVERTER_DIR = Path("kuzushiji-hf-converter")
HF_UPDATE_OUTPUT_DIR = Path("hf_output")
HF_DEFAULT_DATASET_NAME_PREFIX = "kuzushiji-dataset"
HF_DEFAULT_DATASET_TYPE = "both"
HF_DEFAULT_BBOX_FORMAT = "coco"
HF_DEFAULT_MAX_SHARD_SIZE = "500MB"
HF_UPDATE_LOG_TAIL_LINES = 80
DISPLAY_IMAGE_FORMAT_VERSION = "webp1800q75v1"
DISPLAY_IMAGE_MAX_DIM = 1800
DISPLAY_IMAGE_QUALITY = 75

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
    image_version: str
    characters: list[CharInfo]


class ColumnData(BaseModel):
    """列データ（保存用）"""

    char_ids: list[str]


class PredictionFeedback(BaseModel):
    """モデル初期予測に対する human feedback"""

    task_type: str
    source: str
    accepted_count: int
    corrected_count: int
    total_count: int
    page_id: Optional[str] = None
    saved_at: Optional[str] = None


class SaveRequest(BaseModel):
    """保存リクエスト"""

    book_id: str
    page_id: str
    columns: list[ColumnData]
    prediction_feedback: Optional[PredictionFeedback] = None


@dataclass
class AnnotationAlignment:
    """保存済みアノテーションを現在ページへ対応付けた結果"""

    column_by_char_id: dict[str, str]
    segment_by_char_id: dict[str, str]
    saved_char_id_by_current_char_id: dict[str, str]


class HfDatasetUpdateRequest(BaseModel):
    """Hugging Face データセット更新リクエスト"""

    dataset_type: Literal["page", "character", "both"] = HF_DEFAULT_DATASET_TYPE
    bbox_format: Literal["coco", "yolo"] = HF_DEFAULT_BBOX_FORMAT
    dataset_name_prefix: str = HF_DEFAULT_DATASET_NAME_PREFIX
    hub_username: Optional[str] = None
    max_shard_size: str = HF_DEFAULT_MAX_SHARD_SIZE
    dry_run: bool = False


_HF_UPDATE_LOCK = threading.Lock()
_HF_UPDATE_JOB: dict[str, object] = {
    "job_id": None,
    "status": "idle",
    "started": False,
    "started_at": None,
    "finished_at": None,
    "returncode": None,
    "repo_urls": [],
    "repo_candidates": [],
    "log_tail": "",
    "error": None,
    "command": [],
}


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


def _row_text(row: pd.Series, column: str) -> str:
    """行の文字列値を安全に取得"""
    value = row.get(column)
    if pd.isna(value):
        return ""
    return str(value).strip()


def _row_float(row: pd.Series, column: str) -> float:
    """行の数値を安全に取得"""
    value = row.get(column)
    if pd.isna(value):
        return 0.0
    return float(value)


def _annotation_row_fingerprint(row: pd.Series) -> tuple[object, ...]:
    """Char ID を含むページ行の同一性 fingerprint を作る"""
    return (
        _row_text(row, "Char ID"),
        _row_text(row, "Unicode"),
        _row_text(row, "Block ID"),
        int(round(_row_float(row, "X"))),
        int(round(_row_float(row, "Y"))),
        int(round(_row_float(row, "Width"))),
        int(round(_row_float(row, "Height"))),
    )


def _page_fingerprint_matches(current_df: pd.DataFrame, saved_df: pd.DataFrame) -> bool:
    """現在ページと保存済みページが同一行集合かを判定"""
    if len(current_df) != len(saved_df):
        return False

    current_rows = sorted(
        _annotation_row_fingerprint(row) for _, row in current_df.iterrows()
    )
    saved_rows = sorted(_annotation_row_fingerprint(row) for _, row in saved_df.iterrows())
    return current_rows == saved_rows


def _annotation_match_score(current_row: pd.Series, saved_row: pd.Series) -> Optional[float]:
    """現在行と保存済み行の近さを返す。照合不能なら None"""
    if _row_text(current_row, "Unicode") != _row_text(saved_row, "Unicode"):
        return None

    current_block = _row_text(current_row, "Block ID")
    saved_block = _row_text(saved_row, "Block ID")
    if current_block and saved_block and current_block != saved_block:
        return None

    current_cx = _row_float(current_row, "X") + _row_float(current_row, "Width") / 2
    current_cy = _row_float(current_row, "Y") + _row_float(current_row, "Height") / 2
    saved_cx = _row_float(saved_row, "X") + _row_float(saved_row, "Width") / 2
    saved_cy = _row_float(saved_row, "Y") + _row_float(saved_row, "Height") / 2
    dx = abs(current_cx - saved_cx)
    dy = abs(current_cy - saved_cy)
    dw = abs(_row_float(current_row, "Width") - _row_float(saved_row, "Width"))
    dh = abs(_row_float(current_row, "Height") - _row_float(saved_row, "Height"))

    base = max(
        _row_float(current_row, "Width"),
        _row_float(current_row, "Height"),
        _row_float(saved_row, "Width"),
        _row_float(saved_row, "Height"),
        1.0,
    )
    score = (dx + dy + 0.5 * dw + 0.5 * dh) / base
    if score > 0.75:
        return None
    return score


def _match_saved_rows_to_current(
    current_df: pd.DataFrame, saved_df: pd.DataFrame
) -> dict[str, pd.Series]:
    """保存済み行を現在 Char ID へ一対一で対応付ける"""
    candidates: list[tuple[float, str, int, pd.Series]] = []
    scores_by_current: dict[str, list[float]] = {}
    for _, current_row in current_df.iterrows():
        current_char_id = _row_text(current_row, "Char ID")
        if not current_char_id:
            continue
        for saved_pos, (_, saved_row) in enumerate(saved_df.iterrows()):
            score = _annotation_match_score(current_row, saved_row)
            if score is None:
                continue
            candidates.append((score, current_char_id, saved_pos, saved_row))
            scores_by_current.setdefault(current_char_id, []).append(score)

    candidates.sort(key=lambda item: item[0])
    ambiguous_current = {
        char_id
        for char_id, scores in scores_by_current.items()
        if len(scores) > 1 and abs(sorted(scores)[0] - sorted(scores)[1]) < 0.01
    }
    matched_current: set[str] = set()
    matched_saved: set[int] = set()
    result: dict[str, pd.Series] = {}

    for score, current_char_id, saved_pos, saved_row in candidates:
        if current_char_id in ambiguous_current:
            continue
        if current_char_id in matched_current or saved_pos in matched_saved:
            continue

        matched_current.add(current_char_id)
        matched_saved.add(saved_pos)
        result[current_char_id] = saved_row

    return result


def _row_bbox(row: pd.Series) -> tuple[float, float, float, float]:
    """行の bbox を left/top/right/bottom で返す"""
    left = _row_float(row, "X")
    top = _row_float(row, "Y")
    return (
        left,
        top,
        left + _row_float(row, "Width"),
        top + _row_float(row, "Height"),
    )


def _bbox_contains(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> bool:
    """inner bbox が outer bbox に完全に含まれるかを返す"""
    epsilon = 1e-6
    return (
        outer[0] <= inner[0] + epsilon
        and outer[1] <= inner[1] + epsilon
        and outer[2] + epsilon >= inner[2]
        and outer[3] + epsilon >= inner[3]
    )


def _column_bboxes_for_alignment(
    current_df: pd.DataFrame, column_by_char_id: dict[str, str]
) -> dict[str, tuple[float, float, float, float]]:
    """対応済み文字から列 bbox を作る"""
    grouped: dict[str, list[tuple[float, float, float, float]]] = {}
    for _, row in current_df.iterrows():
        char_id = _row_text(row, "Char ID")
        col_id = column_by_char_id.get(char_id)
        if not col_id:
            continue
        grouped.setdefault(col_id, []).append(_row_bbox(row))

    bboxes: dict[str, tuple[float, float, float, float]] = {}
    for col_id, boxes in grouped.items():
        bboxes[col_id] = (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )
    return bboxes


def _fill_unassigned_chars_inside_neighbor_column(
    current_df: pd.DataFrame, alignment: AnnotationAlignment
) -> AnnotationAlignment:
    """前後同列かつ列 bbox 内の未割当文字を同じ列へ補完する"""
    column_by_char_id = dict(alignment.column_by_char_id)
    segment_by_char_id = dict(alignment.segment_by_char_id)
    rows = sorted(
        [row for _, row in current_df.iterrows()],
        key=lambda row: _char_sort_key(_row_text(row, "Char ID")),
    )
    column_bboxes = _column_bboxes_for_alignment(current_df, column_by_char_id)

    for idx in range(1, len(rows) - 1):
        row = rows[idx]
        char_id = _row_text(row, "Char ID")
        if not char_id or char_id in column_by_char_id:
            continue

        prev_char_id = _row_text(rows[idx - 1], "Char ID")
        next_char_id = _row_text(rows[idx + 1], "Char ID")
        prev_col = column_by_char_id.get(prev_char_id)
        next_col = column_by_char_id.get(next_char_id)
        if not prev_col or prev_col != next_col:
            continue

        prev_seg = segment_by_char_id.get(prev_char_id, "")
        next_seg = segment_by_char_id.get(next_char_id, "")
        if prev_seg != next_seg:
            continue

        column_bbox = column_bboxes.get(prev_col)
        if column_bbox is None:
            continue
        if not _bbox_contains(column_bbox, _row_bbox(row)):
            continue

        column_by_char_id[char_id] = prev_col
        if prev_seg:
            segment_by_char_id[char_id] = prev_seg

    return AnnotationAlignment(
        column_by_char_id=column_by_char_id,
        segment_by_char_id=segment_by_char_id,
        saved_char_id_by_current_char_id=alignment.saved_char_id_by_current_char_id,
    )


def align_saved_annotations_to_current_page(
    current_df: pd.DataFrame, saved_df: pd.DataFrame
) -> AnnotationAlignment:
    """保存済み列/セグメントを現在ページの Char ID へ再対応付けする"""
    if saved_df.empty or current_df.empty:
        return AnnotationAlignment({}, {}, {})

    if _page_fingerprint_matches(current_df, saved_df):
        matched_rows = {
            _row_text(row, "Char ID"): row
            for _, row in saved_df.iterrows()
            if _row_text(row, "Char ID")
        }
    else:
        matched_rows = _match_saved_rows_to_current(current_df, saved_df)

    column_by_char_id: dict[str, str] = {}
    segment_by_char_id: dict[str, str] = {}
    saved_char_id_by_current_char_id: dict[str, str] = {}

    for current_char_id, saved_row in matched_rows.items():
        saved_char_id = _row_text(saved_row, "Char ID")
        if saved_char_id:
            saved_char_id_by_current_char_id[current_char_id] = saved_char_id

        col_id = _row_text(saved_row, "Column ID")
        if col_id:
            column_by_char_id[current_char_id] = col_id

        seg_id = _row_text(saved_row, "Segment ID")
        if seg_id:
            segment_by_char_id[current_char_id] = seg_id

    alignment = AnnotationAlignment(
        column_by_char_id=column_by_char_id,
        segment_by_char_id=segment_by_char_id,
        saved_char_id_by_current_char_id=saved_char_id_by_current_char_id,
    )
    return _fill_unassigned_chars_inside_neighbor_column(current_df, alignment)


def _load_saved_page_df(
    csv_path: Path, page_id: str, required_column: str
) -> pd.DataFrame:
    """保存済みCSVから対象ページの行を取得"""
    if not csv_path.exists():
        return pd.DataFrame()

    df = pd.read_csv(csv_path)
    if required_column not in df.columns:
        return pd.DataFrame()
    return cast(pd.DataFrame, df[df["Image"] == page_id].copy())


def load_aligned_existing_annotations(
    book_id: str, page_id: str, current_page_df: pd.DataFrame
) -> AnnotationAlignment:
    """既存アノテーションを現在ページへ安全に対応付けて読み込む"""
    column_df = _load_saved_page_df(
        OUTPUT_DIR / book_id / "column_annotation.csv", page_id, "Column ID"
    )
    segment_df = _load_saved_page_df(
        OUTPUT_SEG_DIR / book_id / "column_annotation.csv", page_id, "Segment ID"
    )

    column_alignment = align_saved_annotations_to_current_page(
        current_page_df, column_df
    )
    segment_alignment = align_saved_annotations_to_current_page(
        current_page_df, segment_df
    )

    return AnnotationAlignment(
        column_by_char_id=column_alignment.column_by_char_id,
        segment_by_char_id=segment_alignment.segment_by_char_id,
        saved_char_id_by_current_char_id={
            **column_alignment.saved_char_id_by_current_char_id,
            **segment_alignment.saved_char_id_by_current_char_id,
        },
    )


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


def build_segment_override_from_saved_annotations(
    current_page_df: pd.DataFrame, saved_segment_df: pd.DataFrame
) -> dict[str, str]:
    """保存済み文字対応から現在 Column ID -> Segment ID を復元"""
    if current_page_df.empty or saved_segment_df.empty:
        return {}
    if "Column ID" not in current_page_df.columns:
        return {}
    if "Segment ID" not in saved_segment_df.columns:
        return {}

    alignment = align_saved_annotations_to_current_page(
        current_page_df, saved_segment_df
    )
    saved_row_by_char_id = {
        _row_text(row, "Char ID"): row
        for _, row in saved_segment_df.iterrows()
        if _row_text(row, "Char ID")
    }

    votes: dict[str, dict[str, int]] = {}
    for _, current_row in current_page_df.iterrows():
        current_col_id = _row_text(current_row, "Column ID")
        current_char_id = _row_text(current_row, "Char ID")
        if not current_col_id or not current_char_id:
            continue

        saved_char_id = alignment.saved_char_id_by_current_char_id.get(
            current_char_id
        )
        if not saved_char_id:
            continue

        saved_row = saved_row_by_char_id.get(saved_char_id)
        if saved_row is None:
            continue

        seg_id = _row_text(saved_row, "Segment ID")
        if not seg_id:
            continue

        votes.setdefault(current_col_id, {})
        votes[current_col_id][seg_id] = votes[current_col_id].get(seg_id, 0) + 1

    override: dict[str, str] = {}
    for col_id, seg_votes in votes.items():
        ranked = sorted(seg_votes.items(), key=lambda item: (-item[1], item[0]))
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            continue
        override[col_id] = ranked[0][0]

    return override


def build_segment_override_for_page(
    book_id: str, page_id: str, current_page_df: pd.DataFrame
) -> dict[str, str]:
    """対象ページの保存済みセグメントから現在列への上書きmapを作る"""
    saved_segment_df = _load_saved_page_df(
        OUTPUT_SEG_DIR / book_id / "column_annotation.csv", page_id, "Segment ID"
    )
    return build_segment_override_from_saved_annotations(
        current_page_df, saved_segment_df
    )


def load_page_index(book_id: str) -> set[str]:
    """アノテーション済みページのインデックスを読み込み（なければ生成）"""
    output_book_dir = OUTPUT_DIR / book_id
    index_path = output_book_dir / INDEX_FILENAME
    output_csv = output_book_dir / "column_annotation.csv"

    if index_path.exists():
        return {
            line.strip() for line in index_path.read_text().splitlines() if line.strip()
        }

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


def _safe_cache_name(value: str) -> str:
    """キャッシュファイル名に使える文字へ置換する"""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def get_display_image_cache_path(book_id: str, page_id: str, source_path: Path) -> Path:
    """表示用圧縮画像キャッシュのパスを取得"""
    version = get_page_image_version(source_path)
    safe_page_id = _safe_cache_name(page_id)
    filename = f"{safe_page_id}.{version}.webp"
    return IMAGE_CACHE_DIR / _safe_cache_name(book_id) / filename


def get_page_image_version(source_path: Path) -> str:
    """画像キャッシュ用のバージョン文字列を取得"""
    stat = source_path.stat()
    return f"{DISPLAY_IMAGE_FORMAT_VERSION}.{stat.st_size}.{stat.st_mtime_ns}"


def ensure_display_image_cache(book_id: str, page_id: str, source_path: Path) -> Path:
    """表示用に縮小・圧縮した画像キャッシュを生成して返す"""
    cache_path = get_display_image_cache_path(book_id, page_id, source_path)
    if cache_path.exists():
        return cache_path

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = cache_path.with_name(f".{cache_path.name}.{uuid.uuid4().hex}.tmp")

    try:
        with Image.open(source_path) as img:
            display_img = img.convert("RGB")
            display_img.thumbnail(
                (DISPLAY_IMAGE_MAX_DIM, DISPLAY_IMAGE_MAX_DIM),
                Image.Resampling.LANCZOS,
            )
            display_img.save(
                tmp_path,
                "WEBP",
                quality=DISPLAY_IMAGE_QUALITY,
                method=4,
            )
        tmp_path.replace(cache_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    return cache_path


def _utc_now_iso() -> str:
    """UTC時刻をISO8601で返す"""
    return datetime.now(timezone.utc).isoformat()


def _project_path(path: Path) -> Path:
    """プロジェクトルート基準の絶対パスを返す"""
    if path.is_absolute():
        return path.resolve()
    return (PROJECT_ROOT / path).resolve()


def _hf_hub_owner(config: HfDatasetUpdateRequest) -> Optional[str]:
    """Hub の owner 候補を返す"""
    return (
        config.hub_username
        or os.environ.get("HF_HUB_USERNAME")
        or os.environ.get("HF_USERNAME")
    )


def build_hf_repo_candidates(config: HfDatasetUpdateRequest) -> list[str]:
    """更新対象になる Hub repo 候補を返す"""
    owner = _hf_hub_owner(config)
    names: list[str] = []
    if config.dataset_type in {"page", "both"}:
        names.append(f"{config.dataset_name_prefix}-{config.bbox_format}")
    if config.dataset_type in {"character", "both"}:
        names.append(f"{config.dataset_name_prefix}-characters")
    if owner:
        return [f"{owner}/{name}" for name in names]
    return names


def build_hf_update_command(config: HfDatasetUpdateRequest) -> list[str]:
    """Hugging Face 更新用の converter コマンドを構築する"""
    converter_dir = _project_path(HF_CONVERTER_DIR)
    command = [
        "uv",
        "run",
        "--project",
        str(converter_dir),
        "python",
        str(converter_dir / "convert_dataset.py"),
        "--export-format",
        "hf",
        "--dataset-type",
        config.dataset_type,
        "--bbox-format",
        config.bbox_format,
        "--dataset-name-prefix",
        config.dataset_name_prefix,
        "--raw-dir",
        str(_project_path(RAW_DIR)),
        "--output-dir",
        str(_project_path(HF_UPDATE_OUTPUT_DIR)),
        "--column-annotations-dir",
        str(_project_path(OUTPUT_DIR)),
        "--segment-annotations-dir",
        str(_project_path(OUTPUT_SEG_DIR)),
        "--push-to-hub",
        "--max-shard-size",
        config.max_shard_size,
    ]
    hub_username = _hf_hub_owner(config)
    if hub_username:
        command.extend(["--hub-username", hub_username])
    if config.dry_run:
        command.append("--dry-run")
    return command


def _copy_hf_update_job() -> dict[str, object]:
    """HF更新ジョブ状態のコピーを返す"""
    payload = dict(_HF_UPDATE_JOB)
    payload["repo_urls"] = list(cast(list[str], payload.get("repo_urls", [])))
    payload["repo_candidates"] = list(
        cast(list[str], payload.get("repo_candidates", []))
    )
    payload["command"] = list(cast(list[str], payload.get("command", [])))
    return payload


def get_hf_update_status_payload() -> dict[str, object]:
    """HF更新ジョブ状態を返す"""
    with _HF_UPDATE_LOCK:
        return _copy_hf_update_job()


def reset_hf_update_state_for_tests() -> None:
    """テスト用に HF 更新状態を初期化する"""
    with _HF_UPDATE_LOCK:
        _HF_UPDATE_JOB.clear()
        _HF_UPDATE_JOB.update(
            {
                "job_id": None,
                "status": "idle",
                "started": False,
                "started_at": None,
                "finished_at": None,
                "returncode": None,
                "repo_urls": [],
                "repo_candidates": [],
                "log_tail": "",
                "error": None,
                "command": [],
            }
        )


def create_hf_update_job(config: HfDatasetUpdateRequest) -> dict[str, object]:
    """HF更新ジョブを作成する（二重起動は拒否）"""
    command = build_hf_update_command(config)
    with _HF_UPDATE_LOCK:
        if _HF_UPDATE_JOB.get("status") == "running":
            payload = _copy_hf_update_job()
            payload["started"] = False
            return payload

        _HF_UPDATE_JOB.clear()
        _HF_UPDATE_JOB.update(
            {
                "job_id": str(uuid.uuid4()),
                "status": "running",
                "started": True,
                "started_at": _utc_now_iso(),
                "finished_at": None,
                "returncode": None,
                "repo_urls": [],
                "repo_candidates": build_hf_repo_candidates(config),
                "log_tail": "",
                "error": None,
                "command": command,
            }
        )
        return _copy_hf_update_job()


def _tail_log(stdout: str, stderr: str) -> str:
    """stdout/stderr の末尾だけを返す"""
    lines = []
    if stdout:
        lines.extend(stdout.splitlines())
    if stderr:
        lines.extend(stderr.splitlines())
    return "\n".join(lines[-HF_UPDATE_LOG_TAIL_LINES:])


def _extract_hf_repo_urls(log_text: str) -> list[str]:
    """converter ログから Hub dataset URL を抽出する"""
    urls = re.findall(r"https://huggingface\.co/datasets/[^\s)]+", log_text)
    return list(dict.fromkeys(urls))


def run_hf_update_job(
    job_id: str,
    config: HfDatasetUpdateRequest,
    runner=subprocess.run,
) -> None:
    """HF更新ジョブを同期実行し、状態を更新する"""
    command = build_hf_update_command(config)
    stdout = ""
    stderr = ""
    try:
        result = runner(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            capture_output=True,
        )
        stdout = str(result.stdout or "")
        stderr = str(result.stderr or "")
        log_tail = _tail_log(stdout, stderr)
        status = "completed" if result.returncode == 0 else "failed"
        with _HF_UPDATE_LOCK:
            if _HF_UPDATE_JOB.get("job_id") != job_id:
                return
            _HF_UPDATE_JOB.update(
                {
                    "status": status,
                    "started": False,
                    "finished_at": _utc_now_iso(),
                    "returncode": int(result.returncode),
                    "repo_urls": _extract_hf_repo_urls(f"{stdout}\n{stderr}"),
                    "log_tail": log_tail,
                    "error": None if result.returncode == 0 else log_tail,
                    "command": command,
                }
            )
    except Exception as exc:
        with _HF_UPDATE_LOCK:
            if _HF_UPDATE_JOB.get("job_id") != job_id:
                return
            _HF_UPDATE_JOB.update(
                {
                    "status": "failed",
                    "started": False,
                    "finished_at": _utc_now_iso(),
                    "returncode": None,
                    "repo_urls": [],
                    "log_tail": _tail_log(stdout, stderr),
                    "error": str(exc),
                    "command": command,
                }
            )


def _model_path(book_id: str, model_name: str) -> Path:
    """学習済みモデルの保存先を返す"""
    return OUTPUT_MODEL_DIR / book_id / f"{model_name}.json"


def _history_path(book_id: str, filename: str) -> Path:
    """履歴ファイルの保存先を返す"""
    return OUTPUT_MODEL_DIR / book_id / filename


def _training_sample_cache_path(book_id: str) -> Path:
    """学習サンプルキャッシュの保存先を返す"""
    return OUTPUT_MODEL_DIR / book_id / TRAINING_SAMPLE_CACHE_FILENAME


def _char_sort_key(char_id: str) -> int:
    """Char ID の数値部を返す"""
    matched = re.search(r"(\d+)", str(char_id))
    if matched is None:
        return 0
    return int(matched.group(1))


def _safe_float(value: object, default: float = 0.0) -> float:
    """数値をfloatへ安全に変換する"""
    if pd.isna(value):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_metric(value: float) -> float:
    """metric表示用に丸める"""
    return round(value, 4)


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    """JSON Lines へ1件追記する"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _load_jsonl(path: Path, limit: Optional[int] = None) -> list[dict[str, object]]:
    """JSON Lines を読み込む"""
    if not path.exists():
        return []

    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        rows.append(cast(dict[str, object], json.loads(stripped)))
    if limit is None or len(rows) <= limit:
        return rows
    return rows[-limit:]


def _char_rect(row: pd.Series) -> tuple[float, float, float, float]:
    """文字bboxを返す"""
    left = _safe_float(row["X"])
    top = _safe_float(row["Y"])
    right = left + max(1.0, _safe_float(row["Width"], 1.0))
    bottom = top + max(1.0, _safe_float(row["Height"], 1.0))
    return left, top, right, bottom


def _interval_overlap_ratio(
    a_min: float,
    a_max: float,
    b_min: float,
    b_max: float,
) -> float:
    """1次元区間の重なり率を返す"""
    inter = max(0.0, min(a_max, b_max) - max(a_min, b_min))
    denom = max(1.0, min(a_max - a_min, b_max - b_min))
    return inter / denom


def _char_boundary_features(
    prev_row: pd.Series,
    cur_row: pd.Series,
    avg_width: float,
    avg_height: float,
) -> dict[str, float]:
    """隣接文字境界の特徴量を返す"""
    pl, pt, pr, pb = _char_rect(prev_row)
    cl, ct, cr, cb = _char_rect(cur_row)
    prev_cx = (pl + pr) / 2
    prev_cy = (pt + pb) / 2
    cur_cx = (cl + cr) / 2
    cur_cy = (ct + cb) / 2
    return {
        "dx": abs(cur_cx - prev_cx) / max(1.0, avg_width),
        "dy": abs(cur_cy - prev_cy) / max(1.0, avg_height),
        "upward": 1.0 if cur_cy < prev_cy else 0.0,
        "x_overlap": _interval_overlap_ratio(pl, pr, cl, cr),
        "y_overlap": _interval_overlap_ratio(pt, pb, ct, cb),
    }


def _column_boundary_samples(
    page_df: pd.DataFrame,
) -> tuple[list[dict[str, float]], list[int]]:
    """列境界学習サンプルを生成する"""
    if "Column ID" not in page_df.columns:
        return [], []

    work_df = page_df.dropna(subset=["Column ID", "Char ID"]).copy()
    if len(work_df) < 2:
        return [], []

    work_df["char_id_num"] = work_df["Char ID"].map(_char_sort_key)
    work_df = work_df.sort_values("char_id_num")
    avg_width = max(1.0, float(work_df["Width"].fillna(0).mean()))
    avg_height = max(1.0, float(work_df["Height"].fillna(0).mean()))

    samples: list[dict[str, float]] = []
    labels: list[int] = []
    rows = list(work_df.iterrows())
    for idx in range(1, len(rows)):
        _, prev_row = rows[idx - 1]
        _, cur_row = rows[idx]
        prev_col = str(prev_row["Column ID"]).strip()
        cur_col = str(cur_row["Column ID"]).strip()
        if not prev_col or not cur_col:
            continue
        samples.append(
            _char_boundary_features(prev_row, cur_row, avg_width, avg_height)
        )
        labels.append(1 if prev_col != cur_col else 0)

    return samples, labels


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
                line.strip()
                for line in index_path.read_text().splitlines()
                if line.strip()
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

        result.append(
            {
                "book_id": book_id,
                "total_pages": total_pages,
                "annotated_count": annotated_count,
                "progress": progress,
            }
        )

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

    # 既存のアノテーションを現在の文字行へ対応付けて読み込み
    existing_alignment = load_aligned_existing_annotations(book_id, page_id, page_df)

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
                column_id=existing_alignment.column_by_char_id.get(str(char_id)),
                segment_id=existing_alignment.segment_by_char_id.get(str(char_id)),
            )
        )

    return PageData(
        book_id=book_id,
        page_id=page_id,
        image_width=image_width,
        image_height=image_height,
        image_version=get_page_image_version(image_path),
        characters=characters,
    )


@app.get("/api/books/{book_id}/pages/{page_id}/image")
async def get_page_image(
    book_id: str,
    page_id: str,
    variant: Literal["display", "original"] = Query("display"),
):
    """ページ画像を返す"""
    image_path = get_image_path(book_id, page_id)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail=f"Image not found: {image_path}")

    if variant == "original":
        return FileResponse(image_path, media_type="image/jpeg")

    display_path = ensure_display_image_cache(book_id, page_id, image_path)
    return FileResponse(display_path, media_type="image/webp")


@app.get("/api/books/{book_id}/pages/{page_id}/suggestions")
async def get_page_suggestions(book_id: str, page_id: str):
    """ページに対する列・セグメント候補を返す"""
    df = load_coordinate_csv(book_id)
    page_df = df[df["Image"] == page_id].copy()
    if page_df.empty:
        raise HTTPException(status_code=404, detail=f"page not found: {page_id}")

    page_df["char_id_num"] = page_df["Char ID"].map(_char_sort_key)
    page_df = page_df.sort_values("char_id_num")

    existing_columns = load_existing_annotations(book_id, page_id)
    if existing_columns:
        return {
            "available": False,
            "reason": "already_annotated",
            "columns": [],
            "segments": [],
        }

    columns_result = _predict_columns_with_model(book_id, page_df)
    segments_result = _predict_segments_with_model(
        book_id,
        page_df,
        cast(list[dict[str, object]], columns_result.get("columns", [])),
    )
    return {
        "available": bool(columns_result.get("available")),
        "columns": columns_result,
        "segments": segments_result,
    }


@app.post("/api/books/{book_id}/models/train")
async def train_models(book_id: str):
    """書籍モデルを再学習する"""
    return train_book_models(book_id)


@app.get("/api/books/{book_id}/models/status")
async def get_model_status(book_id: str):
    """書籍モデルの状態を返す"""
    return build_book_model_status(book_id)


@app.post("/api/huggingface/dataset/update")
async def start_hf_dataset_update(
    background_tasks: BackgroundTasks,
    request: HfDatasetUpdateRequest = HfDatasetUpdateRequest(),
):
    """Hugging Face データセット更新ジョブを開始する"""
    job = create_hf_update_job(request)
    if bool(job.get("started")):
        background_tasks.add_task(run_hf_update_job, str(job["job_id"]), request)
    return job


@app.get("/api/huggingface/dataset/update/status")
async def get_hf_dataset_update_status():
    """Hugging Face データセット更新ジョブの状態を返す"""
    return get_hf_update_status_payload()


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


def load_page_segment_ids(page_df: pd.DataFrame) -> dict[str, str]:
    """ページ内の Column ID -> Segment ID を返す"""
    result: dict[str, str] = {}
    for _, row in page_df.iterrows():
        col_id = row.get("Column ID")
        seg_id = row.get("Segment ID")
        if pd.isna(col_id) or pd.isna(seg_id):
            continue
        col_str = str(col_id).strip()
        seg_str = str(seg_id).strip()
        if col_str and seg_str:
            result[col_str] = seg_str
    return result


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


def _segment_boundary_features(
    prev_box: ColumnBox, cur_box: ColumnBox
) -> dict[str, float]:
    """隣接列境界の特徴量を返す"""
    inter_w = max(
        0.0, min(prev_box.right, cur_box.right) - max(prev_box.left, cur_box.left)
    )
    inter_h = max(
        0.0, min(prev_box.bottom, cur_box.bottom) - max(prev_box.top, cur_box.top)
    )
    inter = inter_w * inter_h
    area_prev = max(1.0, float(prev_box.cw * prev_box.ch))
    area_cur = max(1.0, float(cur_box.cw * cur_box.ch))
    rect_overlap = inter / min(area_prev, area_cur) if inter > 0 else 0.0
    prev_cy = (prev_box.top + prev_box.bottom) / 2
    cur_cy = (cur_box.top + cur_box.bottom) / 2

    return {
        "center_dx": abs(cur_box.cx - prev_box.cx)
        / max(1.0, min(prev_box.cw, cur_box.cw)),
        "center_dy": abs(cur_cy - prev_cy) / max(1.0, min(prev_box.ch, cur_box.ch)),
        "x_overlap": _interval_overlap_ratio(
            float(prev_box.left),
            float(prev_box.right),
            float(cur_box.left),
            float(cur_box.right),
        ),
        "y_overlap": _interval_overlap_ratio(
            float(prev_box.top),
            float(prev_box.bottom),
            float(cur_box.top),
            float(cur_box.bottom),
        ),
        "rect_overlap": rect_overlap,
    }


def _segment_boundary_samples(
    page_df: pd.DataFrame,
) -> tuple[list[dict[str, float]], list[int]]:
    """セグメント境界学習サンプルを生成する"""
    if "Column ID" not in page_df.columns or "Segment ID" not in page_df.columns:
        return [], []

    boxes = _compute_column_boxes(page_df)
    if len(boxes) < 2:
        return [], []

    seg_map = load_page_segment_ids(page_df)
    samples: list[dict[str, float]] = []
    labels: list[int] = []
    for idx in range(1, len(boxes)):
        prev_box = boxes[idx - 1]
        cur_box = boxes[idx]
        prev_seg = seg_map.get(prev_box.column_id, "")
        cur_seg = seg_map.get(cur_box.column_id, "")
        if not prev_seg or not cur_seg:
            continue
        samples.append(_segment_boundary_features(prev_box, cur_box))
        labels.append(1 if prev_seg != cur_seg else 0)
    return samples, labels


def _classification_metrics(
    model: dict[str, object],
    samples: list[dict[str, float]],
    labels: list[int],
) -> dict[str, float]:
    """2値分類の簡易 metrics を返す"""
    if not samples or not labels or len(samples) != len(labels):
        return {}

    tp = 0
    tn = 0
    fp = 0
    fn = 0
    for sample, label in zip(samples, labels, strict=False):
        predicted = 1 if _score_boundary(model, sample) >= 0.5 else 0
        if predicted == 1 and label == 1:
            tp += 1
        elif predicted == 0 and label == 0:
            tn += 1
        elif predicted == 1 and label == 0:
            fp += 1
        else:
            fn += 1

    total = tp + tn + fp + fn
    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return {
        "accuracy": _round_metric(accuracy),
        "precision": _round_metric(precision),
        "recall": _round_metric(recall),
        "f1": _round_metric(f1),
    }


def _fit_boundary_model(
    book_id: str,
    model_name: str,
    samples: list[dict[str, float]],
    labels: list[int],
) -> dict[str, object]:
    """境界分類用の簡易統計モデルを学習する"""
    feature_names = list(samples[0].keys()) if samples else []
    payload: dict[str, object] = {
        "book_id": book_id,
        "model_name": model_name,
        "version": MODEL_VERSION,
        "trained_at": _utc_now_iso(),
        "available": False,
        "sample_count": len(samples),
        "feature_names": feature_names,
        "metrics": {},
    }

    pos_idx = [idx for idx, label in enumerate(labels) if label == 1]
    neg_idx = [idx for idx, label in enumerate(labels) if label == 0]
    if len(samples) < MIN_MODEL_SAMPLES or len(pos_idx) < 2 or len(neg_idx) < 2:
        return payload

    classes: dict[str, object] = {}
    total = len(samples)
    for class_name, indices in {"break": pos_idx, "keep": neg_idx}.items():
        means: dict[str, float] = {}
        stds: dict[str, float] = {}
        for feature_name in feature_names:
            values = [samples[idx][feature_name] for idx in indices]
            mean_val = sum(values) / len(values)
            variance = sum((value - mean_val) ** 2 for value in values) / max(
                1, len(values) - 1
            )
            means[feature_name] = mean_val
            stds[feature_name] = max(0.1, math.sqrt(max(variance, 0.0)))
        classes[class_name] = {
            "count": len(indices),
            "prior": len(indices) / total,
            "means": means,
            "stds": stds,
        }

    payload["available"] = True
    payload["classes"] = classes
    payload["metrics"] = _classification_metrics(payload, samples, labels)
    return payload


def _save_model_payload(
    book_id: str, model_name: str, payload: dict[str, object]
) -> None:
    """モデルJSONを保存する"""
    model_path = _model_path(book_id, model_name)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    model_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_model_payload(book_id: str, model_name: str) -> dict[str, object]:
    """モデルJSONを読み込む"""
    model_path = _model_path(book_id, model_name)
    if not model_path.exists():
        return {
            "book_id": book_id,
            "model_name": model_name,
            "version": MODEL_VERSION,
            "available": False,
            "sample_count": 0,
        }
    return cast(dict[str, object], json.loads(model_path.read_text(encoding="utf-8")))


def _score_boundary(model: dict[str, object], sample: dict[str, float]) -> float:
    """境界がbreakである確率を返す"""
    if not bool(model.get("available")):
        return 0.0

    classes = cast(dict[str, dict[str, object]], model.get("classes", {}))
    if not classes:
        return 0.0

    scores: dict[str, float] = {}
    for class_name in ("break", "keep"):
        class_info = classes.get(class_name)
        if not class_info:
            continue
        means = cast(dict[str, float], class_info.get("means", {}))
        stds = cast(dict[str, float], class_info.get("stds", {}))
        prior = max(1e-6, float(class_info.get("prior", 1e-6)))
        score = math.log(prior)
        for feature_name, value in sample.items():
            mean_val = float(means.get(feature_name, 0.0))
            std_val = max(0.1, float(stds.get(feature_name, 1.0)))
            z = (value - mean_val) / std_val
            score -= 0.5 * (z * z)
        scores[class_name] = score

    if "break" not in scores or "keep" not in scores:
        return 0.0

    max_score = max(scores.values())
    exp_break = math.exp(scores["break"] - max_score)
    exp_keep = math.exp(scores["keep"] - max_score)
    return exp_break / max(1e-6, exp_break + exp_keep)


def _record_training_event(
    book_id: str,
    column_model: dict[str, object],
    segment_model: dict[str, object],
) -> None:
    """再学習イベントを履歴に追記する"""
    _append_jsonl(
        _history_path(book_id, TRAINING_HISTORY_FILENAME),
        {
            "recorded_at": _utc_now_iso(),
            "book_id": book_id,
            "column_model": {
                "available": bool(column_model.get("available")),
                "sample_count": int(column_model.get("sample_count", 0)),
                "trained_at": column_model.get("trained_at"),
                "metrics": column_model.get("metrics", {}),
            },
            "segment_model": {
                "available": bool(segment_model.get("available")),
                "sample_count": int(segment_model.get("sample_count", 0)),
                "trained_at": segment_model.get("trained_at"),
                "metrics": segment_model.get("metrics", {}),
            },
        },
    )


def _collect_book_training_samples(
    book_id: str,
) -> tuple[
    list[dict[str, float]],
    list[int],
    list[dict[str, float]],
    list[int],
]:
    """指定書籍の学習サンプルを収集する"""
    column_output_csv = OUTPUT_DIR / book_id / "column_annotation.csv"
    segment_output_csv = OUTPUT_SEG_DIR / book_id / "column_annotation.csv"

    column_samples: list[dict[str, float]] = []
    column_labels: list[int] = []
    if column_output_csv.exists():
        column_df = pd.read_csv(column_output_csv)
        for _, page_df in column_df.groupby("Image", sort=False):
            samples, labels = _column_boundary_samples(page_df)
            column_samples.extend(samples)
            column_labels.extend(labels)

    segment_samples: list[dict[str, float]] = []
    segment_labels: list[int] = []
    if segment_output_csv.exists():
        segment_df = pd.read_csv(segment_output_csv)
        for _, page_df in segment_df.groupby("Image", sort=False):
            samples, labels = _segment_boundary_samples(page_df)
            segment_samples.extend(samples)
            segment_labels.extend(labels)

    return column_samples, column_labels, segment_samples, segment_labels


def _save_book_training_samples_cache(
    book_id: str,
    column_samples: list[dict[str, float]],
    column_labels: list[int],
    segment_samples: list[dict[str, float]],
    segment_labels: list[int],
) -> None:
    """書籍ごとの学習サンプルをキャッシュする"""
    cache_path = _training_sample_cache_path(book_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "book_id": book_id,
                "cached_at": _utc_now_iso(),
                "column_samples": column_samples,
                "column_labels": column_labels,
                "segment_samples": segment_samples,
                "segment_labels": segment_labels,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _load_book_training_samples_cache(
    book_id: str,
) -> tuple[
    list[dict[str, float]],
    list[int],
    list[dict[str, float]],
    list[int],
] | None:
    """書籍ごとの学習サンプルキャッシュを読む"""
    cache_path = _training_sample_cache_path(book_id)
    if not cache_path.exists():
        return None

    payload = cast(dict[str, object], json.loads(cache_path.read_text(encoding="utf-8")))
    return (
        cast(list[dict[str, float]], payload.get("column_samples", [])),
        [int(v) for v in cast(list[object], payload.get("column_labels", []))],
        cast(list[dict[str, float]], payload.get("segment_samples", [])),
        [int(v) for v in cast(list[object], payload.get("segment_labels", []))],
    )


def _collect_global_training_samples() -> tuple[
    list[dict[str, float]],
    list[int],
    list[dict[str, float]],
    list[int],
]:
    """全書籍の学習サンプルをキャッシュから収集する"""
    all_column_samples: list[dict[str, float]] = []
    all_column_labels: list[int] = []
    all_segment_samples: list[dict[str, float]] = []
    all_segment_labels: list[int] = []

    for book_id in get_book_dirs():
        cached = _load_book_training_samples_cache(book_id)
        if cached is None:
            column_samples, column_labels, segment_samples, segment_labels = (
                _collect_book_training_samples(book_id)
            )
            _save_book_training_samples_cache(
                book_id,
                column_samples,
                column_labels,
                segment_samples,
                segment_labels,
            )
        else:
            column_samples, column_labels, segment_samples, segment_labels = cached
        all_column_samples.extend(column_samples)
        all_column_labels.extend(column_labels)
        all_segment_samples.extend(segment_samples)
        all_segment_labels.extend(segment_labels)

    return (
        all_column_samples,
        all_column_labels,
        all_segment_samples,
        all_segment_labels,
    )


def _record_feedback_event(book_id: str, payload: dict[str, object]) -> None:
    """HITL フィードバックを履歴に追記する"""
    accepted_count = max(0, int(payload.get("accepted_count", 0)))
    corrected_count = max(0, int(payload.get("corrected_count", 0)))
    total_count = max(0, int(payload.get("total_count", 0)))
    if total_count <= 0:
        return

    source = str(payload.get("source", "")).strip()
    if not source:
        return

    task_type = str(payload.get("task_type", "")).strip()
    if task_type not in {"column", "segment"}:
        return

    event = {
        "recorded_at": _utc_now_iso(),
        "book_id": book_id,
        "task_type": task_type,
        "source": source,
        "accepted_count": accepted_count,
        "corrected_count": corrected_count,
        "total_count": total_count,
        "page_id": str(payload.get("page_id", "")).strip(),
        "saved_at": payload.get("saved_at") or _utc_now_iso(),
    }
    _append_jsonl(_history_path(book_id, FEEDBACK_HISTORY_FILENAME), event)
    if source.startswith("global_") and book_id != GLOBAL_MODEL_BOOK_ID:
        global_event = dict(event)
        global_event["book_id"] = GLOBAL_MODEL_BOOK_ID
        _append_jsonl(
            _history_path(GLOBAL_MODEL_BOOK_ID, FEEDBACK_HISTORY_FILENAME), global_event
        )


def _empty_feedback_summary(task_type: str) -> dict[str, object]:
    """空の feedback summary を返す"""
    return {
        "task_type": task_type,
        "event_count": 0,
        "accepted_count": 0,
        "corrected_count": 0,
        "total_count": 0,
        "acceptance_rate": 0.0,
        "last_page_id": None,
        "last_recorded_at": None,
    }


def _summarize_feedback_events(
    events: list[dict[str, object]], task_type: str
) -> dict[str, object]:
    """task_type ごとの feedback summary を返す"""
    filtered = [
        event for event in events if str(event.get("task_type", "")).strip() == task_type
    ]
    if not filtered:
        return _empty_feedback_summary(task_type)

    accepted_count = sum(int(event.get("accepted_count", 0)) for event in filtered)
    corrected_count = sum(int(event.get("corrected_count", 0)) for event in filtered)
    total_count = sum(int(event.get("total_count", 0)) for event in filtered)
    latest = filtered[-1]
    return {
        "task_type": task_type,
        "event_count": len(filtered),
        "accepted_count": accepted_count,
        "corrected_count": corrected_count,
        "total_count": total_count,
        "acceptance_rate": _round_metric(
            accepted_count / total_count if total_count else 0.0
        ),
        "last_page_id": latest.get("page_id") or None,
        "last_recorded_at": latest.get("recorded_at"),
    }


def _model_status_payload(
    book_id: str,
    model_name: str,
    task_type: str,
    feedback_events: list[dict[str, object]],
) -> dict[str, object]:
    """モデル状態のレスポンスを構築する"""
    model = _load_model_payload(book_id, model_name)
    return {
        "book_id": book_id,
        "model_name": model_name,
        "task_type": task_type,
        "available": bool(model.get("available")),
        "sample_count": int(model.get("sample_count", 0)),
        "trained_at": model.get("trained_at"),
        "metrics": cast(dict[str, float], model.get("metrics", {})),
        "feedback_summary": _summarize_feedback_events(feedback_events, task_type),
    }


def _resolve_model_payload(
    book_id: str, model_name: str
) -> tuple[dict[str, object], str]:
    """書籍モデルを優先し、なければ global モデルへフォールバックする"""
    book_model = _load_model_payload(book_id, model_name)
    if bool(book_model.get("available")):
        return book_model, "book"

    global_model = _load_model_payload(GLOBAL_MODEL_BOOK_ID, model_name)
    if bool(global_model.get("available")):
        return global_model, "global"

    return book_model, "none"


def build_book_model_status(book_id: str) -> dict[str, object]:
    """書籍のモデル状態を返す"""
    all_feedback_events = _load_jsonl(_history_path(book_id, FEEDBACK_HISTORY_FILENAME))
    all_training_history = _load_jsonl(_history_path(book_id, TRAINING_HISTORY_FILENAME))
    global_feedback_events = _load_jsonl(
        _history_path(GLOBAL_MODEL_BOOK_ID, FEEDBACK_HISTORY_FILENAME)
    )
    book_column_model = _model_status_payload(
        book_id, "column_boundary_model", "column", all_feedback_events
    )
    book_segment_model = _model_status_payload(
        book_id, "segment_boundary_model", "segment", all_feedback_events
    )
    global_column_model = _model_status_payload(
        GLOBAL_MODEL_BOOK_ID,
        "column_boundary_model",
        "column",
        global_feedback_events,
    )
    global_segment_model = _model_status_payload(
        GLOBAL_MODEL_BOOK_ID,
        "segment_boundary_model",
        "segment",
        global_feedback_events,
    )
    effective_column_source = (
        "book"
        if bool(book_column_model.get("available"))
        else "global"
        if bool(global_column_model.get("available"))
        else "none"
    )
    effective_segment_source = (
        "book"
        if bool(book_segment_model.get("available"))
        else "global"
        if bool(global_segment_model.get("available"))
        else "none"
    )
    return {
        "book_id": book_id,
        "column_model": book_column_model,
        "segment_model": book_segment_model,
        "global_column_model": global_column_model,
        "global_segment_model": global_segment_model,
        "effective_column_source": effective_column_source,
        "effective_segment_source": effective_segment_source,
        "recent_feedback": all_feedback_events[-HISTORY_PREVIEW_LIMIT:],
        "recent_training": all_training_history[-HISTORY_PREVIEW_LIMIT:],
    }


def train_book_models(book_id: str) -> dict[str, object]:
    """書籍単位と全書籍共通の列/セグメント境界モデルを学習する"""
    column_samples, column_labels, segment_samples, segment_labels = (
        _collect_book_training_samples(book_id)
    )
    _save_book_training_samples_cache(
        book_id,
        column_samples,
        column_labels,
        segment_samples,
        segment_labels,
    )
    column_model = _fit_boundary_model(
        book_id, "column_boundary_model", column_samples, column_labels
    )
    segment_model = _fit_boundary_model(
        book_id,
        "segment_boundary_model",
        segment_samples,
        segment_labels,
    )
    _save_model_payload(book_id, "column_boundary_model", column_model)
    _save_model_payload(book_id, "segment_boundary_model", segment_model)
    _record_training_event(book_id, column_model, segment_model)

    (
        global_column_samples,
        global_column_labels,
        global_segment_samples,
        global_segment_labels,
    ) = _collect_global_training_samples()
    global_column_model = _fit_boundary_model(
        GLOBAL_MODEL_BOOK_ID,
        "column_boundary_model",
        global_column_samples,
        global_column_labels,
    )
    global_segment_model = _fit_boundary_model(
        GLOBAL_MODEL_BOOK_ID,
        "segment_boundary_model",
        global_segment_samples,
        global_segment_labels,
    )
    _save_model_payload(
        GLOBAL_MODEL_BOOK_ID, "column_boundary_model", global_column_model
    )
    _save_model_payload(
        GLOBAL_MODEL_BOOK_ID, "segment_boundary_model", global_segment_model
    )
    _record_training_event(
        GLOBAL_MODEL_BOOK_ID, global_column_model, global_segment_model
    )

    return {
        "book_id": book_id,
        "column_model": column_model,
        "segment_model": segment_model,
        "global_column_model": global_column_model,
        "global_segment_model": global_segment_model,
    }


def _segments_from_mapping(mapping: dict[str, str], source: str) -> dict[str, object]:
    """Column ID -> Segment ID をレスポンス形式へ変換する"""
    grouped: dict[str, list[str]] = {}
    for col_id, seg_id in mapping.items():
        grouped.setdefault(seg_id, []).append(col_id)

    items = sorted(grouped.items(), key=lambda item: item[0])
    return {
        "available": bool(items),
        "source": source,
        "segments": [
            {"segment_id": seg_id, "column_ids": col_ids} for seg_id, col_ids in items
        ],
        "boundaries": [],
    }


def _predict_columns_with_model(
    book_id: str, page_df: pd.DataFrame
) -> dict[str, object]:
    """学習済みモデルで列を推論する"""
    model, model_scope = _resolve_model_payload(book_id, "column_boundary_model")
    work_df = page_df.copy()
    if work_df.empty:
        return {"available": False, "columns": [], "boundaries": []}

    work_df["char_id_num"] = work_df["Char ID"].map(_char_sort_key)
    work_df = work_df.sort_values("char_id_num")
    rows = list(work_df.iterrows())
    if not bool(model.get("available")) or len(rows) == 0:
        return {
            "available": False,
            "source": "fallback",
            "columns": [],
            "boundaries": [],
        }

    avg_width = max(1.0, float(work_df["Width"].fillna(0).mean()))
    avg_height = max(1.0, float(work_df["Height"].fillna(0).mean()))
    current_column = [str(rows[0][1]["Char ID"])]
    columns: list[dict[str, object]] = []
    boundaries: list[dict[str, object]] = []

    for idx in range(1, len(rows)):
        _, prev_row = rows[idx - 1]
        _, cur_row = rows[idx]
        sample = _char_boundary_features(prev_row, cur_row, avg_width, avg_height)
        break_score = _score_boundary(model, sample)
        should_break = break_score >= 0.5
        boundaries.append(
            {
                "left_char_id": str(prev_row["Char ID"]),
                "right_char_id": str(cur_row["Char ID"]),
                "break_score": round(break_score, 4),
                "predicted_break": should_break,
            }
        )
        if should_break:
            columns.append(
                {
                    "column_id": f"PRED_COL{len(columns) + 1:04d}",
                    "char_ids": current_column,
                }
            )
            current_column = [str(cur_row["Char ID"])]
        else:
            current_column.append(str(cur_row["Char ID"]))

    columns.append(
        {
            "column_id": f"PRED_COL{len(columns) + 1:04d}",
            "char_ids": current_column,
        }
    )

    return {
        "available": True,
        "source": (
            "column_boundary_model"
            if model_scope == "book"
            else "global_column_boundary_model"
        ),
        "model_scope": model_scope,
        "model_book_id": model.get("book_id"),
        "trained_at": model.get("trained_at"),
        "sample_count": model.get("sample_count", 0),
        "columns": columns,
        "boundaries": boundaries,
    }


def _predict_segments_with_model(
    book_id: str,
    page_df: pd.DataFrame,
    predicted_columns: list[dict[str, object]],
) -> dict[str, object]:
    """学習済みモデルでセグメントを推論する"""
    if not predicted_columns:
        return {"available": False, "segments": [], "boundaries": []}

    model, model_scope = _resolve_model_payload(book_id, "segment_boundary_model")
    pred_df = page_df.copy()
    pred_df["Column ID"] = None
    for idx, column in enumerate(predicted_columns, start=1):
        col_id = f"COL{idx:04d}"
        char_ids = cast(list[str], column.get("char_ids", []))
        pred_df.loc[pred_df["Char ID"].isin(char_ids), "Column ID"] = col_id

    boxes = _compute_column_boxes(pred_df)
    if len(boxes) == 0:
        return {"available": False, "segments": [], "boundaries": []}

    if not bool(model.get("available")):
        heuristic_map = _estimate_segments_for_page(pred_df)
        return _segments_from_mapping(heuristic_map, "heuristic_segment_estimator")

    current_segment = [boxes[0].column_id]
    segments: list[list[str]] = []
    boundaries: list[dict[str, object]] = []
    for idx in range(1, len(boxes)):
        prev_box = boxes[idx - 1]
        cur_box = boxes[idx]
        break_score = _score_boundary(
            model, _segment_boundary_features(prev_box, cur_box)
        )
        should_break = break_score >= 0.5
        boundaries.append(
            {
                "left_column_id": prev_box.column_id,
                "right_column_id": cur_box.column_id,
                "break_score": round(break_score, 4),
                "predicted_break": should_break,
            }
        )
        if should_break:
            segments.append(current_segment)
            current_segment = [cur_box.column_id]
        else:
            current_segment.append(cur_box.column_id)
    segments.append(current_segment)

    return {
        "available": True,
        "source": (
            "segment_boundary_model"
            if model_scope == "book"
            else "global_segment_boundary_model"
        ),
        "model_scope": model_scope,
        "model_book_id": model.get("book_id"),
        "trained_at": model.get("trained_at"),
        "sample_count": model.get("sample_count", 0),
        "segments": [
            {
                "segment_id": f"SEG{idx:04d}",
                "column_ids": col_ids,
            }
            for idx, col_ids in enumerate(segments, start=1)
        ],
        "boundaries": boundaries,
    }


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

    def overlap_ratio(
        a: tuple[int, int, int, int], b: tuple[int, int, int, int]
    ) -> float:
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

        if any(
            overlap_ratio(cand_rect, r) > rect_overlap_th_closed for r in closed_rects
        ):
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
    with (
        out_csv.open("r", encoding="utf-8", newline="") as src,
        tmp_path.open("w", encoding="utf-8", newline="") as dst,
    ):
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
    if request.book_id != book_id or request.page_id != page_id:
        raise HTTPException(status_code=400, detail="book_id/page_id mismatch")

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
        with (
            output_csv.open("r", encoding="utf-8", newline="") as src,
            tmp_path.open("w", encoding="utf-8", newline="") as dst,
        ):
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
    background_tasks.add_task(
        generate_column_images, book_id, page_id, columns_char_ids
    )
    background_tasks.add_task(train_book_models, book_id)
    if request.prediction_feedback is not None:
        _record_feedback_event(book_id, request.prediction_feedback.model_dump())

    # 既存のセグメント（手動編集）を可能な限り保持
    segment_override = build_segment_override_for_page(book_id, page_id, page_df)
    if not segment_override:
        segment_override = load_existing_segment_map(book_id, page_id)
    _update_output_seg_csv(
        book_id, page_id, page_df, segment_map_override=segment_override or None
    )

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
    prediction_feedback: Optional[PredictionFeedback] = None


@app.post("/api/books/{book_id}/pages/{page_id}/save_segments")
async def save_segments(
    book_id: str,
    page_id: str,
    request: SaveSegmentsRequest,
    background_tasks: BackgroundTasks,
):
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
        raise HTTPException(
            status_code=404, detail=f"page not found in annotations: {page_id}"
        )

    if "Column ID" not in page_df.columns:
        raise HTTPException(status_code=400, detail="Column ID column not found")

    page_cols = {
        str(v).strip()
        for v in page_df["Column ID"].dropna().unique().tolist()
        if str(v).strip()
    }
    if not page_cols:
        raise HTTPException(status_code=400, detail="no Column ID found for this page")

    provided_cols = set(seg_map.keys())
    unknown = sorted(provided_cols - page_cols)
    missing = sorted(page_cols - provided_cols)
    if unknown:
        raise HTTPException(
            status_code=400, detail=f"unknown Column ID(s): {', '.join(unknown)}"
        )
    if missing:
        raise HTTPException(
            status_code=400, detail=f"missing Column ID(s): {', '.join(missing)}"
        )

    _update_output_seg_csv(book_id, page_id, page_df, segment_map_override=seg_map)
    background_tasks.add_task(train_book_models, book_id)
    if request.prediction_feedback is not None:
        _record_feedback_event(book_id, request.prediction_feedback.model_dump())

    out_csv = OUTPUT_SEG_DIR / book_id / "column_annotation.csv"
    return {
        "success": True,
        "message": f"セグメント保存完了: {len(request.segments)}件",
        "csv_path": str(out_csv),
    }


# ===== メイン =====
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=APP_RELOAD,
        reload_dirs=APP_RELOAD_DIRS,
        reload_excludes=APP_RELOAD_EXCLUDES,
    )
