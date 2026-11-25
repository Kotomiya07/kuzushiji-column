"""性能最適化（キャッシュ・無効化・画像キャッシュヘッダ・モデル学習一致）のテスト。"""

import asyncio
from pathlib import Path
import sys

from fastapi import BackgroundTasks
from PIL import Image, features
import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def _setup_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(main, "RAW_DIR", raw_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(main, "OUTPUT_SEG_DIR", tmp_path / "output_seg")
    monkeypatch.setattr(main, "OUTPUT_MODEL_DIR", tmp_path / "output_models")
    monkeypatch.setattr(main, "IMAGE_CACHE_DIR", tmp_path / "cache")

    book_dir = raw_dir / "book1"
    (book_dir / "images").mkdir(parents=True, exist_ok=True)
    rows = ["Image,Unicode,X,Y,Width,Height,Char ID,Block ID"]
    for page_id in ("p1", "p2"):
        Image.new("RGB", (400, 300), (240, 240, 230)).save(
            book_dir / "images" / f"{page_id}.jpg", "JPEG"
        )
        rows += [
            f"{page_id},U+4E00,300,50,20,20,C0001,",
            f"{page_id},U+4E8C,300,80,20,20,C0002,",
            f"{page_id},U+4E09,200,50,20,20,C0003,",
            f"{page_id},U+56DB,200,80,20,20,C0004,",
        ]
    (book_dir / "book1_coordinate.csv").write_text("\n".join(rows), encoding="utf-8")
    return book_dir


def test_books_summary_cache_hits_and_invalidates_on_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_book(tmp_path, monkeypatch)

    calls = 0
    orig_get_completed = main.get_completed_pages

    def counting_completed(book_id: str) -> set[str]:
        nonlocal calls
        calls += 1
        return orig_get_completed(book_id)

    monkeypatch.setattr(main, "get_completed_pages", counting_completed)

    first = asyncio.run(main.get_books())["books"]
    second = asyncio.run(main.get_books())["books"]
    assert first == second
    assert first[0]["annotated_count"] == 0
    assert first[0]["completed_count"] == 0
    assert calls == 1

    box1 = main.ColumnBoxData(x=290, y=40, width=80, height=70)
    box2 = main.ColumnBoxData(x=190, y=40, width=80, height=70)
    req = main.SaveRequest(
        book_id="book1",
        page_id="p1",
        columns=[
            main.ColumnData(char_ids=["C0001", "C0002"], box=box1),
            main.ColumnData(char_ids=["C0003", "C0004"], box=box2),
        ],
    )
    asyncio.run(main.save_annotations("book1", "p1", req, BackgroundTasks()))

    third = asyncio.run(main.get_books())["books"]
    assert third[0]["annotated_count"] == 1
    assert third[0]["completed_count"] == 1
    assert third[0]["progress"] == 50
    assert calls == 2

    fourth = asyncio.run(main.get_books())["books"]
    assert fourth == third
    assert calls == 2


def test_page_image_sets_cache_control_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not features.check("webp"):
        pytest.skip("Pillow WebP support is not available")
    _setup_book(tmp_path, monkeypatch)

    res_no_ver = asyncio.run(main.get_page_image("book1", "p1", variant="display", v=None))
    assert "max-age=60" in res_no_ver.headers.get("cache-control", "")

    res_with_ver = asyncio.run(
        main.get_page_image("book1", "p1", variant="display", v="v123")
    )
    assert "immutable" in res_with_ver.headers.get("cache-control", "")
    assert "max-age=31536000" in res_with_ver.headers.get("cache-control", "")
