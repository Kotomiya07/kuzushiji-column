from pathlib import Path
import asyncio
import sys

import pandas as pd
import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from PIL import Image

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main

IMAGE_SIZE = (400, 300)
BOX_COLUMNS = ["Column X", "Column Y", "Column Width", "Column Height"]


def _setup_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """ページ画像と座標CSVを tmp に用意し、出力先を差し替える"""
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(main, "RAW_DIR", raw_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(main, "OUTPUT_SEG_DIR", tmp_path / "output_seg")

    book_dir = raw_dir / "book1"
    for page_id in ("page1", "page2"):
        image_path = book_dir / "images" / f"{page_id}.jpg"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", IMAGE_SIZE, (240, 240, 230)).save(image_path, "JPEG")

    rows = ["Image,Unicode,X,Y,Width,Height,Char ID,Block ID"]
    for page_id in ("page1", "page2"):
        rows += [
            f"{page_id},U+4E00,300,50,20,20,C0001,",
            f"{page_id},U+4E8C,300,80,20,20,C0002,",
            f"{page_id},U+4E09,200,50,20,20,C0003,",
            f"{page_id},U+56DB,200,80,20,20,C0004,",
        ]
    (book_dir / "book1_coordinate.csv").write_text("\n".join(rows), encoding="utf-8")
    return book_dir


def _save(
    columns: list[main.ColumnData], page_id: str = "page1"
) -> dict[str, object]:
    request = main.SaveRequest(book_id="book1", page_id=page_id, columns=columns)
    return asyncio.run(
        main.save_annotations("book1", page_id, request, BackgroundTasks())
    )


def _two_columns(
    box1: main.ColumnBoxData | None = None, box2: main.ColumnBoxData | None = None
) -> list[main.ColumnData]:
    return [
        main.ColumnData(char_ids=["C0001", "C0002"], box=box1),
        main.ColumnData(char_ids=["C0003", "C0004"], box=box2),
    ]


def test_save_preserves_existing_column_images_without_generating_new_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    columns_dir = tmp_path / "output" / "book1" / "columns"
    columns_dir.mkdir(parents=True)
    existing_crop = columns_dir / "page1_col0001.jpg"
    Image.new("RGB", (10, 10)).save(existing_crop)
    existing_bytes = existing_crop.read_bytes()
    trained_books: list[str] = []
    monkeypatch.setattr(main, "train_book_models", trained_books.append)
    request = main.SaveRequest(book_id="book1", page_id="page1", columns=_two_columns())

    with TestClient(main.app) as client:
        response = client.post(
            "/api/books/book1/pages/page1/save", json=request.model_dump(mode="json")
        )

    assert response.status_code == 200
    assert existing_crop.read_bytes() == existing_bytes
    assert {path.name for path in columns_dir.iterdir()} == {existing_crop.name}
    assert trained_books == ["book1"]
    assert response.json()["message"] == "保存完了: 2列"


def test_save_with_box_writes_csv_columns_and_restores_column_boxes(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    box = main.ColumnBoxData(x=290, y=40, width=80, height=70)

    _save(_two_columns(box1=box))

    df = pd.read_csv(tmp_path / "output" / "book1" / "column_annotation.csv")
    col1 = df[df["Column ID"] == "COL0001"]
    assert len(col1) == 2
    for column, expected in zip(BOX_COLUMNS, (290, 40, 80, 70)):
        assert col1[column].tolist() == [expected, expected]
    col2 = df[df["Column ID"] == "COL0002"]
    assert col2[BOX_COLUMNS].isna().all().all()

    data = asyncio.run(main.get_page_data("book1", "page1"))
    assert set(data.column_boxes) == {"COL0001"}
    assert data.column_boxes["COL0001"] == box


def test_save_without_box_leaves_columns_blank(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)

    _save(_two_columns())

    df = pd.read_csv(tmp_path / "output" / "book1" / "column_annotation.csv")
    assert set(BOX_COLUMNS) <= set(df.columns)
    assert df[BOX_COLUMNS].isna().all().all()
    assert df["Column ID"].tolist() == ["COL0001", "COL0001", "COL0002", "COL0002"]

    data = asyncio.run(main.get_page_data("book1", "page1"))
    assert data.column_boxes == {}
    assert [c.column_id for c in data.characters] == [
        "COL0001",
        "COL0001",
        "COL0002",
        "COL0002",
    ]


def test_save_clamps_box_to_image_bounds(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    box = main.ColumnBoxData(x=-20, y=-5, width=1000, height=1000)

    _save(_two_columns(box1=box))

    data = asyncio.run(main.get_page_data("book1", "page1"))
    clamped = data.column_boxes["COL0001"]
    assert clamped.x == 0
    assert clamped.y == 0
    assert clamped.x + clamped.width <= IMAGE_SIZE[0]
    assert clamped.y + clamped.height <= IMAGE_SIZE[1]
    assert (clamped.width, clamped.height) == IMAGE_SIZE


def test_save_clamps_box_starting_past_right_edge(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    box = main.ColumnBoxData(x=395, y=10, width=50, height=20)

    _save(_two_columns(box1=box))

    clamped = asyncio.run(main.get_page_data("book1", "page1")).column_boxes["COL0001"]
    assert clamped.x + clamped.width <= IMAGE_SIZE[0]
    assert clamped.width >= 1


def test_box_rejects_non_positive_size() -> None:
    with pytest.raises(ValueError):
        main.ColumnBoxData(x=0, y=0, width=0, height=10)
    with pytest.raises(ValueError):
        main.ColumnBoxData(x=0, y=0, width=10, height=0)


def test_resave_without_box_clears_previous_box(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save(_two_columns(box1=main.ColumnBoxData(x=290, y=40, width=80, height=70)))

    _save(_two_columns())

    data = asyncio.run(main.get_page_data("book1", "page1"))
    assert data.column_boxes == {}


def test_output_seg_csv_inherits_column_box_columns(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    box = main.ColumnBoxData(x=290, y=40, width=80, height=70)

    _save(_two_columns(box1=box))

    seg_df = pd.read_csv(tmp_path / "output_seg" / "book1" / "column_annotation.csv")
    col1 = seg_df[seg_df["Column ID"] == "COL0001"]
    assert col1["Column X"].tolist() == [290, 290]
    assert col1["Column Width"].tolist() == [80, 80]
    col2 = seg_df[seg_df["Column ID"] == "COL0002"]
    assert col2[BOX_COLUMNS].isna().all().all()


def test_new_page_append_realigns_legacy_csv_without_box_columns(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    legacy = (
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID\n"
        "page2,U+4E00,300,50,20,20,C0001,,COL0001\n"
        "page2,U+4E8C,300,80,20,20,C0002,,COL0001\n"
        "page2,U+4E09,200,50,20,20,C0003,,COL0002\n"
        "page2,U+56DB,200,80,20,20,C0004,,COL0002\n"
    )
    (out_dir / "column_annotation.csv").write_text(legacy, encoding="utf-8")
    (out_dir / main.INDEX_FILENAME).write_text("page2\n", encoding="utf-8")

    box = main.ColumnBoxData(x=290, y=40, width=80, height=70)
    _save(_two_columns(box1=box), page_id="page1")

    df = pd.read_csv(out_dir / "column_annotation.csv")
    assert len(df) == 8
    assert set(BOX_COLUMNS) <= set(df.columns)
    page2 = df[df["Image"] == "page2"]
    assert len(page2) == 4
    assert page2[BOX_COLUMNS].isna().all().all()
    assert page2["Column ID"].tolist() == ["COL0001", "COL0001", "COL0002", "COL0002"]
    page1 = df[(df["Image"] == "page1") & (df["Column ID"] == "COL0001")]
    assert page1["Column X"].tolist() == [290, 290]
    assert page1["Column Height"].tolist() == [70, 70]

    # 旧ページは新列が空のまま、新ページには box が復元される
    assert asyncio.run(main.get_page_data("book1", "page2")).column_boxes == {}
    restored = asyncio.run(main.get_page_data("book1", "page1")).column_boxes
    assert restored["COL0001"] == box


def test_legacy_csv_without_box_columns_still_loads(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    legacy = (
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID\n"
        "page1,U+4E00,300,50,20,20,C0001,,COL0001\n"
        "page1,U+4E8C,300,80,20,20,C0002,,COL0001\n"
    )
    (out_dir / "column_annotation.csv").write_text(legacy, encoding="utf-8")

    data = asyncio.run(main.get_page_data("book1", "page1"))

    assert data.column_boxes == {}
    assert data.characters[0].column_id == "COL0001"


def test_incomplete_box_values_are_not_restored(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    partial = (
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID,"
        "Column X,Column Y,Column Width,Column Height\n"
        "page1,U+4E00,300,50,20,20,C0001,,COL0001,290,40,,\n"
        "page1,U+4E8C,300,80,20,20,C0002,,COL0001,290,40,,\n"
    )
    (out_dir / "column_annotation.csv").write_text(partial, encoding="utf-8")

    data = asyncio.run(main.get_page_data("book1", "page1"))

    assert data.column_boxes == {}


def test_generate_column_images_without_box_uses_character_union(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)

    main.generate_column_images("book1", "page1", [["C0001", "C0002"]])

    crop_path = tmp_path / "output" / "book1" / "columns" / "page1_col0001.jpg"
    with Image.open(crop_path) as crop:
        # 文字の外接 x 300-320, y 50-100 に padding 10
        assert crop.size == (40, 70)


def test_generate_column_images_uses_box_as_crop_base(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    boxes: list[main.ColumnBoxData | None] = [
        main.ColumnBoxData(x=100, y=40, width=100, height=60),
        None,
    ]

    main.generate_column_images(
        "book1", "page1", [["C0001", "C0002"], ["C0003", "C0004"]], boxes
    )

    columns_dir = tmp_path / "output" / "book1" / "columns"
    with Image.open(columns_dir / "page1_col0001.jpg") as crop:
        # box 100x60 に padding 10 を四方へ
        assert crop.size == (120, 80)
    with Image.open(columns_dir / "page1_col0002.jpg") as crop:
        # box なしの列は文字の外接 x 200-220, y 50-100 に padding 10
        assert crop.size == (40, 70)


def test_generate_column_images_clamps_box_with_padding_to_image(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    boxes: list[main.ColumnBoxData | None] = [
        main.ColumnBoxData(x=0, y=0, width=IMAGE_SIZE[0], height=IMAGE_SIZE[1])
    ]

    main.generate_column_images("book1", "page1", [["C0001"]], boxes)

    crop_path = tmp_path / "output" / "book1" / "columns" / "page1_col0001.jpg"
    with Image.open(crop_path) as crop:
        assert crop.size == IMAGE_SIZE
