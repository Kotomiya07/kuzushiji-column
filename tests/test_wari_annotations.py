"""割注 (Wari ID / Wari Part) の保存・復元・検証テスト。"""

from pathlib import Path
import asyncio
import sys

import pandas as pd
import pytest
from fastapi import BackgroundTasks, HTTPException
from PIL import Image

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main

IMAGE_SIZE = (400, 300)
WARI_COLUMNS = ["Wari ID", "Wari Part"]


# ===== ヘルパー =====
def _setup_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """ページ画像と座標CSVを tmp に用意し、出力先を差し替える

    列1 (x=300) は C0001-C0006、列2 (x=200) は C0007-C0008 を想定する。
    """
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
    unicodes = [
        "U+4E00",
        "U+4E8C",
        "U+4E09",
        "U+56DB",
        "U+4E94",
        "U+516D",
        "U+4E03",
        "U+516B",
    ]
    for page_id in ("page1", "page2"):
        for index, unicode in enumerate(unicodes):
            if index < 6:
                x, y = 300, 50 + 30 * index
            else:
                x, y = 200, 50 + 30 * (index - 6)
            rows.append(f"{page_id},{unicode},{x},{y},20,20,C{index + 1:04d},")
    (book_dir / "book1_coordinate.csv").write_text("\n".join(rows), encoding="utf-8")
    return book_dir


def _columns() -> list[main.ColumnData]:
    return [
        main.ColumnData(char_ids=[f"C{i:04d}" for i in range(1, 7)]),
        main.ColumnData(char_ids=["C0007", "C0008"]),
    ]


def _wari(char_id: str, wari_id: str, part: str) -> main.WariAssignment:
    return main.WariAssignment(char_id=char_id, wari_id=wari_id, wari_part=part)


def _two_groups() -> list[main.WariAssignment]:
    """一時 ID は保存時に振り直される (z -> WARI0001, a -> WARI0002 になるはず)"""
    return [
        _wari("C0005", "a", "right"),
        _wari("C0006", "a", "left"),
        _wari("C0002", "z", "right"),
        _wari("C0003", "z", "left"),
    ]


def _save(
    columns: list[main.ColumnData],
    wari: list[main.WariAssignment] | None = None,
    page_id: str = "page1",
) -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if wari is not None:
        kwargs["wari"] = wari
    request = main.SaveRequest(
        book_id="book1", page_id=page_id, columns=columns, **kwargs
    )
    return asyncio.run(
        main.save_annotations("book1", page_id, request, BackgroundTasks())
    )


def _output_csv(tmp_path: Path) -> Path:
    return tmp_path / "output" / "book1" / "column_annotation.csv"


def _seg_csv(tmp_path: Path) -> Path:
    return tmp_path / "output_seg" / "book1" / "column_annotation.csv"


# ===== モデル =====
def test_save_request_wari_defaults_to_empty_list() -> None:
    request = main.SaveRequest(book_id="book1", page_id="page1", columns=[])

    assert request.wari == []


def test_char_info_wari_fields_are_optional() -> None:
    info = main.CharInfo(
        unicode="U+4E00",
        image="page1",
        x=0,
        y=0,
        block_id="",
        char_id="C0001",
        width=10,
        height=10,
    )

    assert info.wari_id is None
    assert info.wari_part is None


# ===== 保存と復元 =====
def test_save_with_wari_writes_renumbered_csv_columns_and_restores(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)

    _save(_columns(), _two_groups())

    df = pd.read_csv(_output_csv(tmp_path))
    assert df["Char ID"].tolist() == [f"C{i:04d}" for i in range(1, 9)]
    assert df["Wari ID"].fillna("").tolist() == [
        "",
        "WARI0001",
        "WARI0001",
        "",
        "WARI0002",
        "WARI0002",
        "",
        "",
    ]
    assert df["Wari Part"].fillna("").tolist() == [
        "",
        "right",
        "left",
        "",
        "right",
        "left",
        "",
        "",
    ]

    data = asyncio.run(main.get_page_data("book1", "page1"))
    restored = {c.char_id: (c.wari_id, c.wari_part) for c in data.characters}
    assert restored["C0001"] == (None, None)
    assert restored["C0002"] == ("WARI0001", "right")
    assert restored["C0003"] == ("WARI0001", "left")
    assert restored["C0005"] == ("WARI0002", "right")
    assert restored["C0006"] == ("WARI0002", "left")
    assert restored["C0008"] == (None, None)


def test_save_wari_group_with_multiple_chars_per_part(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    wari = [
        _wari("C0002", "g", "right"),
        _wari("C0003", "g", "right"),
        _wari("C0004", "g", "left"),
        _wari("C0005", "g", "left"),
    ]

    _save(_columns(), wari)

    data = asyncio.run(main.get_page_data("book1", "page1"))
    parts = {c.char_id: c.wari_part for c in data.characters}
    assert [parts[f"C{i:04d}"] for i in range(2, 6)] == [
        "right",
        "right",
        "left",
        "left",
    ]


def test_save_without_wari_leaves_new_columns_blank(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)

    _save(_columns())

    df = pd.read_csv(_output_csv(tmp_path))
    assert set(WARI_COLUMNS) <= set(df.columns)
    assert df[WARI_COLUMNS].isna().all().all()
    assert df["Column ID"].tolist() == ["COL0001"] * 6 + ["COL0002"] * 2

    data = asyncio.run(main.get_page_data("book1", "page1"))
    assert all(c.wari_id is None and c.wari_part is None for c in data.characters)
    assert [c.column_id for c in data.characters] == ["COL0001"] * 6 + ["COL0002"] * 2


def test_resave_without_wari_clears_previous_wari(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save(_columns(), _two_groups())

    _save(_columns(), [])

    df = pd.read_csv(_output_csv(tmp_path))
    assert df[WARI_COLUMNS].isna().all().all()
    data = asyncio.run(main.get_page_data("book1", "page1"))
    assert all(c.wari_id is None for c in data.characters)


def test_wari_ids_are_renumbered_per_page(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)

    _save(_columns(), _two_groups(), page_id="page1")
    _save(
        _columns(),
        [_wari("C0004", "x", "right"), _wari("C0005", "x", "left")],
        page_id="page2",
    )

    df = pd.read_csv(_output_csv(tmp_path))
    page2 = df[df["Image"] == "page2"]
    assert page2["Wari ID"].dropna().unique().tolist() == ["WARI0001"]
    page1 = df[df["Image"] == "page1"]
    assert page1["Wari ID"].dropna().unique().tolist() == ["WARI0001", "WARI0002"]


# ===== 検証 (HTTP 400) =====
@pytest.mark.parametrize(
    "wari",
    [
        # 同じ char_id への重複割当
        [
            _wari("C0002", "g1", "right"),
            _wari("C0002", "g2", "left"),
            _wari("C0003", "g2", "left"),
        ],
        # 1文字だけのグループ
        [_wari("C0002", "g", "right")],
        # right だけ
        [_wari("C0002", "g", "right"), _wari("C0003", "g", "right")],
        # left だけ
        [_wari("C0002", "g", "left"), _wari("C0003", "g", "left")],
        # 複数列にまたがる
        [_wari("C0006", "g", "right"), _wari("C0007", "g", "left")],
        # left の後に right
        [_wari("C0002", "g", "left"), _wari("C0003", "g", "right")],
        # right と left が交互
        [
            _wari("C0002", "g", "right"),
            _wari("C0003", "g", "left"),
            _wari("C0004", "g", "right"),
        ],
        # 列に属さない char_id
        [_wari("C0002", "g", "right"), _wari("C9999", "g", "left")],
        # 不正な part
        [_wari("C0002", "g", "right"), _wari("C0003", "g", "middle")],
    ],
    ids=[
        "duplicate-assignment",
        "single-char",
        "right-only",
        "left-only",
        "cross-column",
        "left-before-right",
        "right-after-left",
        "unknown-char",
        "invalid-part",
    ],
)
def test_invalid_wari_is_rejected_with_400(
    tmp_path, monkeypatch, wari: list[main.WariAssignment]
) -> None:
    _setup_book(tmp_path, monkeypatch)

    with pytest.raises(HTTPException) as exc_info:
        _save(_columns(), wari)

    assert exc_info.value.status_code == 400
    assert not _output_csv(tmp_path).exists()


def test_invalid_wari_keeps_previous_saved_data(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save(_columns(), _two_groups())

    with pytest.raises(HTTPException):
        _save(_columns(), [_wari("C0002", "g", "right")])

    df = pd.read_csv(_output_csv(tmp_path))
    assert df["Wari ID"].dropna().unique().tolist() == ["WARI0001", "WARI0002"]


# ===== output_seg への引き継ぎ =====
def test_output_seg_csv_inherits_wari_columns(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)

    _save(_columns(), _two_groups())

    seg_df = pd.read_csv(_seg_csv(tmp_path))
    assert set(WARI_COLUMNS) <= set(seg_df.columns)
    assert seg_df["Wari ID"].fillna("").tolist() == [
        "",
        "WARI0001",
        "WARI0001",
        "",
        "WARI0002",
        "WARI0002",
        "",
        "",
    ]
    assert seg_df["Wari Part"].fillna("").tolist()[1:3] == ["right", "left"]


def test_save_segments_keeps_wari_and_writes_no_nan_text(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save(_columns(), _two_groups())

    request = main.SaveSegmentsRequest(
        book_id="book1",
        page_id="page1",
        segments=[
            main.SegmentData(segment_id="SEG0001", column_ids=["COL0001"]),
            main.SegmentData(segment_id="SEG0002", column_ids=["COL0002"]),
        ],
    )
    asyncio.run(
        main.save_segments("book1", "page1", request, BackgroundTasks())
    )

    seg_df = pd.read_csv(_seg_csv(tmp_path))
    assert seg_df["Wari ID"].fillna("").tolist()[1:3] == ["WARI0001", "WARI0001"]
    assert seg_df["Wari Part"].fillna("").tolist()[1:3] == ["right", "left"]
    # Wari 列の空欄が "nan" 文字列で書かれていないこと (生の CSV を文字列で読む)
    raw = pd.read_csv(_seg_csv(tmp_path), dtype=str, keep_default_na=False)
    assert raw["Wari ID"].tolist() == [
        "",
        "WARI0001",
        "WARI0001",
        "",
        "WARI0002",
        "WARI0002",
        "",
        "",
    ]
    assert raw["Wari Part"].tolist()[0] == ""


def test_output_seg_wari_is_used_when_column_csv_has_none(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    seg_dir = tmp_path / "output_seg" / "book1"
    seg_dir.mkdir(parents=True)
    (seg_dir / "column_annotation.csv").write_text(
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID,Segment ID,"
        "Wari ID,Wari Part\n"
        "page1,U+4E00,300,50,20,20,C0001,,COL0001,SEG0001,,\n"
        "page1,U+4E8C,300,80,20,20,C0002,,COL0001,SEG0001,WARI0001,right\n"
        "page1,U+4E09,300,110,20,20,C0003,,COL0001,SEG0001,WARI0001,left\n",
        encoding="utf-8",
    )

    data = asyncio.run(main.get_page_data("book1", "page1"))

    restored = {c.char_id: (c.wari_id, c.wari_part) for c in data.characters}
    assert restored["C0002"] == ("WARI0001", "right")
    assert restored["C0003"] == ("WARI0001", "left")
    assert restored["C0001"] == (None, None)


def test_column_csv_wari_takes_precedence_over_segment_csv(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    header = (
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID,%s"
        "Wari ID,Wari Part\n"
    )
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    (out_dir / "column_annotation.csv").write_text(
        (header % "")
        + "page1,U+4E00,300,50,20,20,C0001,,COL0001,WARI0001,right\n"
        + "page1,U+4E8C,300,80,20,20,C0002,,COL0001,WARI0001,left\n",
        encoding="utf-8",
    )
    seg_dir = tmp_path / "output_seg" / "book1"
    seg_dir.mkdir(parents=True)
    (seg_dir / "column_annotation.csv").write_text(
        (header % "Segment ID,").replace("Wari ID,Wari Part\n", "Wari ID,Wari Part\n")
        .replace("Column ID,Segment ID,", "Column ID,Segment ID,")
        + "page1,U+4E00,300,50,20,20,C0001,,COL0001,SEG0001,WARI0009,left\n"
        + "page1,U+4E8C,300,80,20,20,C0002,,COL0001,SEG0001,WARI0009,right\n"
        + "page1,U+4E09,300,110,20,20,C0003,,COL0001,SEG0001,WARI0005,right\n",
        encoding="utf-8",
    )

    data = asyncio.run(main.get_page_data("book1", "page1"))

    restored = {c.char_id: (c.wari_id, c.wari_part) for c in data.characters}
    # 列 CSV 側を優先
    assert restored["C0001"] == ("WARI0001", "right")
    assert restored["C0002"] == ("WARI0001", "left")
    # 列 CSV に無い文字はセグメント CSV で補う
    assert restored["C0003"] == ("WARI0005", "right")


# ===== 後方互換 =====
def test_legacy_csv_without_wari_columns_still_loads(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    (out_dir / "column_annotation.csv").write_text(
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID\n"
        "page1,U+4E00,300,50,20,20,C0001,,COL0001\n"
        "page1,U+4E8C,300,80,20,20,C0002,,COL0001\n",
        encoding="utf-8",
    )

    data = asyncio.run(main.get_page_data("book1", "page1"))

    assert data.characters[0].column_id == "COL0001"
    assert all(c.wari_id is None and c.wari_part is None for c in data.characters)


def test_blank_wari_values_are_ignored_on_restore(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    (out_dir / "column_annotation.csv").write_text(
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID,Wari ID,Wari Part\n"
        "page1,U+4E00,300,50,20,20,C0001,,COL0001,,\n"
        "page1,U+4E8C,300,80,20,20,C0002,,COL0001,WARI0001,\n",
        encoding="utf-8",
    )

    data = asyncio.run(main.get_page_data("book1", "page1"))

    assert all(c.wari_id is None and c.wari_part is None for c in data.characters)


def test_new_page_append_realigns_legacy_csv_without_wari_columns(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    legacy_rows = [
        f"page2,U+4E00,300,{50 + 30 * i},20,20,C{i + 1:04d},,COL0001" for i in range(6)
    ]
    (out_dir / "column_annotation.csv").write_text(
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID\n"
        + "\n".join(legacy_rows)
        + "\n",
        encoding="utf-8",
    )
    (out_dir / main.INDEX_FILENAME).write_text("page2\n", encoding="utf-8")

    _save(_columns(), _two_groups(), page_id="page1")

    df = pd.read_csv(out_dir / "column_annotation.csv")
    assert set(WARI_COLUMNS) <= set(df.columns)
    page2 = df[df["Image"] == "page2"]
    assert len(page2) == 6
    assert page2[WARI_COLUMNS].isna().all().all()
    assert page2["Column ID"].tolist() == ["COL0001"] * 6
    page1 = df[df["Image"] == "page1"]
    assert page1["Wari ID"].dropna().tolist() == ["WARI0001"] * 2 + ["WARI0002"] * 2


def test_resave_existing_page_realigns_legacy_csv_without_wari_columns(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    out_dir = tmp_path / "output" / "book1"
    out_dir.mkdir(parents=True)
    legacy_rows = [
        f"page1,U+4E00,300,{50 + 30 * i},20,20,C{i + 1:04d},,COL0001" for i in range(6)
    ]
    legacy_rows.append("page2,U+4E00,300,50,20,20,C0001,,COL0001")
    (out_dir / "column_annotation.csv").write_text(
        "Image,Unicode,X,Y,Width,Height,Char ID,Block ID,Column ID\n"
        + "\n".join(legacy_rows)
        + "\n",
        encoding="utf-8",
    )
    (out_dir / main.INDEX_FILENAME).write_text("page1\npage2\n", encoding="utf-8")

    _save(_columns(), _two_groups(), page_id="page1")

    df = pd.read_csv(out_dir / "column_annotation.csv")
    assert df[df["Image"] == "page2"][WARI_COLUMNS].isna().all().all()
    page1 = df[df["Image"] == "page1"]
    assert page1["Wari Part"].dropna().tolist() == ["right", "left"] * 2


# ===== AnnotationAlignment =====
def test_alignment_reads_wari_and_carries_it_through_neighbor_fill() -> None:
    current = pd.DataFrame(
        {
            "Image": ["page1"] * 4,
            "Char ID": ["C0001", "C0002", "C0003", "C0004"],
            "Unicode": ["U+4E00", "U+4E8C", "U+4E09", "U+56DB"],
            "X": [300, 300, 300, 300],
            "Y": [50, 80, 110, 140],
            "Width": [20] * 4,
            "Height": [20] * 4,
        }
    )
    # C0003 は列未割当 (前後が同じ列かつ bbox 内なので近傍補完される)
    saved = current.copy()
    saved["Column ID"] = ["COL0001", "COL0001", None, "COL0001"]
    saved["Wari ID"] = [None, "WARI0001", None, "WARI0001"]
    saved["Wari Part"] = [None, "right", None, "left"]

    alignment = main.align_saved_annotations_to_current_page(current, saved)

    assert alignment.column_by_char_id["C0003"] == "COL0001"
    assert alignment.wari_id_by_char_id == {"C0002": "WARI0001", "C0004": "WARI0001"}
    assert alignment.wari_part_by_char_id == {"C0002": "right", "C0004": "left"}


def test_alignment_empty_result_has_empty_wari_maps() -> None:
    alignment = main.align_saved_annotations_to_current_page(
        pd.DataFrame(), pd.DataFrame()
    )

    assert alignment.wari_id_by_char_id == {}
    assert alignment.wari_part_by_char_id == {}
