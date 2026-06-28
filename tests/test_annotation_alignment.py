from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def _row(
    char_id: str,
    unicode: str,
    x: int,
    y: int,
    column_id: str | None = None,
    segment_id: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "Image": "page_001",
        "Unicode": unicode,
        "X": x,
        "Y": y,
        "Width": 20,
        "Height": 30,
        "Char ID": char_id,
        "Block ID": "",
    }
    if column_id is not None:
        row["Column ID"] = column_id
    if segment_id is not None:
        row["Segment ID"] = segment_id
    return row


def test_align_saved_annotations_keeps_existing_chars_after_mid_insert() -> None:
    saved_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10, "COL0001", "SEG0001"),
            _row("C0002", "U+4E01", 300, 50, "COL0001", "SEG0001"),
            _row("C0003", "U+4E02", 220, 10, "COL0002", "SEG0002"),
        ]
    )
    current_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10),
            _row("C0002", "U+9999", 260, 80),
            _row("C0003", "U+4E01", 300, 50),
            _row("C0004", "U+4E02", 220, 10),
        ]
    )

    aligned = main.align_saved_annotations_to_current_page(current_df, saved_df)

    assert aligned.column_by_char_id == {
        "C0001": "COL0001",
        "C0003": "COL0001",
        "C0004": "COL0002",
    }
    assert aligned.segment_by_char_id == {
        "C0001": "SEG0001",
        "C0003": "SEG0001",
        "C0004": "SEG0002",
    }
    assert "C0002" not in aligned.column_by_char_id


def test_align_saved_annotations_adds_new_char_inside_neighbor_column() -> None:
    saved_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10, "COL0001", "SEG0001"),
            _row("C0002", "U+4E01", 300, 90, "COL0001", "SEG0001"),
            _row("C0003", "U+4E02", 220, 10, "COL0002", "SEG0002"),
        ]
    )
    current_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10),
            _row("C0002", "U+9999", 300, 50),
            _row("C0003", "U+4E01", 300, 90),
            _row("C0004", "U+4E02", 220, 10),
        ]
    )

    aligned = main.align_saved_annotations_to_current_page(current_df, saved_df)

    assert aligned.column_by_char_id["C0002"] == "COL0001"
    assert aligned.segment_by_char_id["C0002"] == "SEG0001"


def test_align_saved_annotations_leaves_new_char_unassigned_when_outside_column_bbox() -> None:
    saved_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10, "COL0001", "SEG0001"),
            _row("C0002", "U+4E01", 300, 90, "COL0001", "SEG0001"),
        ]
    )
    current_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10),
            _row("C0002", "U+9999", 340, 50),
            _row("C0003", "U+4E01", 300, 90),
        ]
    )

    aligned = main.align_saved_annotations_to_current_page(current_df, saved_df)

    assert "C0002" not in aligned.column_by_char_id
    assert "C0002" not in aligned.segment_by_char_id


def test_build_segment_override_preserves_segments_by_matched_char_majority() -> None:
    saved_seg_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10, "COL0001", "SEG0001"),
            _row("C0002", "U+4E01", 300, 50, "COL0001", "SEG0001"),
            _row("C0003", "U+4E02", 220, 10, "COL0002", "SEG0002"),
        ]
    )
    current_df = pd.DataFrame(
        [
            _row("C0001", "U+4E00", 300, 10, "COL0001"),
            _row("C0002", "U+9999", 260, 80, "COL0002"),
            _row("C0003", "U+4E01", 300, 50, "COL0001"),
            _row("C0004", "U+4E02", 220, 10, "COL0003"),
        ]
    )

    override = main.build_segment_override_from_saved_annotations(
        current_df,
        saved_seg_df,
    )

    assert override == {
        "COL0001": "SEG0001",
        "COL0003": "SEG0002",
    }
    assert "COL0002" not in override
