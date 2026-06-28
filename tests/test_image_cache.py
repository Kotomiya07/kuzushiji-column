from pathlib import Path
import asyncio
import sys

import pytest
from PIL import Image, features

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def _require_webp() -> None:
    if not features.check("webp"):
        pytest.skip("Pillow WebP support is not available")


def _write_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=95)


def test_ensure_display_image_cache_creates_and_reuses_webp(tmp_path, monkeypatch) -> None:
    _require_webp()
    monkeypatch.setattr(main, "IMAGE_CACHE_DIR", tmp_path / "cache")
    source_path = tmp_path / "raw" / "book1" / "images" / "page1.jpg"
    _write_image(source_path, (2400, 1200), (240, 240, 230))

    cache_path = main.ensure_display_image_cache("book1", "page1", source_path)
    first_mtime = cache_path.stat().st_mtime_ns
    second_path = main.ensure_display_image_cache("book1", "page1", source_path)

    assert second_path == cache_path
    assert second_path.stat().st_mtime_ns == first_mtime
    assert cache_path.suffix == ".webp"

    with Image.open(cache_path) as img:
        assert img.size == (1800, 900)


def test_display_image_cache_version_changes_when_source_changes(tmp_path, monkeypatch) -> None:
    _require_webp()
    monkeypatch.setattr(main, "IMAGE_CACHE_DIR", tmp_path / "cache")
    source_path = tmp_path / "raw" / "book1" / "images" / "page1.jpg"
    _write_image(source_path, (1200, 800), (220, 220, 220))

    first_version = main.get_page_image_version(source_path)
    first_cache = main.ensure_display_image_cache("book1", "page1", source_path)

    _write_image(source_path, (1200, 800), (20, 20, 20))
    second_version = main.get_page_image_version(source_path)
    second_cache = main.ensure_display_image_cache("book1", "page1", source_path)

    assert second_version != first_version
    assert second_cache != first_cache
    assert second_cache.exists()


def test_get_page_data_keeps_original_image_dimensions_and_returns_version(
    tmp_path, monkeypatch
) -> None:
    _require_webp()
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    output_seg_dir = tmp_path / "output_seg"
    monkeypatch.setattr(main, "RAW_DIR", raw_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(main, "OUTPUT_SEG_DIR", output_seg_dir)

    book_dir = raw_dir / "book1"
    image_path = book_dir / "images" / "page1.jpg"
    _write_image(image_path, (2400, 1200), (240, 240, 230))
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "book1_coordinate.csv").write_text(
        "\n".join(
            [
                "Image,Unicode,X,Y,Width,Height,Char ID,Block ID",
                "page1,U+4E00,100,200,30,40,C0001,",
            ]
        ),
        encoding="utf-8",
    )

    data = asyncio.run(main.get_page_data("book1", "page1"))

    assert data.image_width == 2400
    assert data.image_height == 1200
    assert data.image_version == main.get_page_image_version(image_path)


def test_page_image_endpoint_returns_display_webp_and_original_jpeg(
    tmp_path, monkeypatch
) -> None:
    _require_webp()
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(main, "RAW_DIR", raw_dir)
    monkeypatch.setattr(main, "IMAGE_CACHE_DIR", tmp_path / "cache")

    image_path = raw_dir / "book1" / "images" / "page1.jpg"
    _write_image(image_path, (1200, 800), (240, 240, 230))

    display_res = asyncio.run(main.get_page_image("book1", "page1"))
    original_res = asyncio.run(main.get_page_image("book1", "page1", variant="original"))

    assert display_res.media_type == "image/webp"
    assert original_res.media_type == "image/jpeg"
