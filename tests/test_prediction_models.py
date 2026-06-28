from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main


def _make_column_rows(image: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    specs = [
        ("C0001", "COL0001", 300, 20),
        ("C0002", "COL0001", 300, 80),
        ("C0003", "COL0002", 220, 25),
        ("C0004", "COL0002", 220, 85),
        ("C0005", "COL0003", 140, 30),
        ("C0006", "COL0003", 140, 90),
    ]
    for char_id, column_id, x, y in specs:
        rows.append(
            {
                "Image": image,
                "Unicode": "U+4E00",
                "X": x,
                "Y": y,
                "Width": 20,
                "Height": 40,
                "Char ID": char_id,
                "Block ID": "",
                "Column ID": column_id,
            }
        )
    return rows


def _make_segment_rows(image: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    specs = [
        ("C0001", "COL0001", 300, 20),
        ("C0002", "COL0001", 300, 80),
        ("C0003", "COL0002", 294, 180),
        ("C0004", "COL0002", 294, 240),
        ("C0005", "COL0003", 140, 25),
        ("C0006", "COL0003", 140, 85),
    ]
    for char_id, column_id, x, y in specs:
        rows.append(
            {
                "Image": image,
                "Unicode": "U+4E00",
                "X": x,
                "Y": y,
                "Width": 20,
                "Height": 40,
                "Char ID": char_id,
                "Block ID": "",
                "Column ID": column_id,
            }
        )
    return rows


def test_column_boundary_model_predicts_breaks(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path

    train_df = pd.DataFrame(_make_column_rows("page_a") + _make_column_rows("page_b"))
    samples: list[dict[str, float]] = []
    labels: list[int] = []
    for _, page_df in train_df.groupby("Image", sort=False):
        page_samples, page_labels = main._column_boundary_samples(page_df)
        samples.extend(page_samples)
        labels.extend(page_labels)

    model = main._fit_boundary_model("book1", "column_boundary_model", samples, labels)
    assert model["available"] is True
    main._save_model_payload("book1", "column_boundary_model", model)

    pred_df = pd.DataFrame(_make_column_rows("page_pred")).drop(columns=["Column ID"])
    result = main._predict_columns_with_model("book1", pred_df)

    assert result["available"] is True
    columns = result["columns"]
    assert [len(col["char_ids"]) for col in columns] == [2, 2, 2]


def test_segment_boundary_model_predicts_groups(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path

    train_rows = []
    for image in ("page_a", "page_b", "page_c", "page_d"):
        for row in _make_segment_rows(image):
            row = dict(row)
            if row["Column ID"] in {"COL0001", "COL0002"}:
                row["Segment ID"] = "SEG0001"
            else:
                row["Segment ID"] = "SEG0002"
            train_rows.append(row)

    train_df = pd.DataFrame(train_rows)
    samples: list[dict[str, float]] = []
    labels: list[int] = []
    for _, page_df in train_df.groupby("Image", sort=False):
        page_samples, page_labels = main._segment_boundary_samples(page_df)
        samples.extend(page_samples)
        labels.extend(page_labels)

    model = main._fit_boundary_model("book1", "segment_boundary_model", samples, labels)
    assert model["available"] is True
    main._save_model_payload("book1", "segment_boundary_model", model)

    pred_df = pd.DataFrame(_make_segment_rows("page_pred")).drop(columns=["Column ID"])
    predicted_columns = [
        {"column_id": "PRED_COL0001", "char_ids": ["C0001", "C0002"]},
        {"column_id": "PRED_COL0002", "char_ids": ["C0003", "C0004"]},
        {"column_id": "PRED_COL0003", "char_ids": ["C0005", "C0006"]},
    ]
    result = main._predict_segments_with_model("book1", pred_df, predicted_columns)

    assert result["available"] is True
    assert result["segments"][0]["column_ids"] == ["COL0001", "COL0002"]
    assert result["segments"][1]["column_ids"] == ["COL0003"]


def test_column_prediction_falls_back_to_global_model(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path

    train_df = pd.DataFrame(_make_column_rows("page_a") + _make_column_rows("page_b"))
    samples: list[dict[str, float]] = []
    labels: list[int] = []
    for _, page_df in train_df.groupby("Image", sort=False):
        page_samples, page_labels = main._column_boundary_samples(page_df)
        samples.extend(page_samples)
        labels.extend(page_labels)

    model = main._fit_boundary_model(
        main.GLOBAL_MODEL_BOOK_ID, "column_boundary_model", samples, labels
    )
    assert model["available"] is True
    main._save_model_payload(main.GLOBAL_MODEL_BOOK_ID, "column_boundary_model", model)

    pred_df = pd.DataFrame(_make_column_rows("page_pred")).drop(columns=["Column ID"])
    result = main._predict_columns_with_model("book_without_model", pred_df)

    assert result["available"] is True
    assert result["source"] == "global_column_boundary_model"
    assert result["model_scope"] == "global"
    assert result["model_book_id"] == main.GLOBAL_MODEL_BOOK_ID
