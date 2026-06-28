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


def _train_column_model(book_id: str) -> dict[str, object]:
    train_df = pd.DataFrame(_make_column_rows("page_a") + _make_column_rows("page_b"))
    samples: list[dict[str, float]] = []
    labels: list[int] = []
    for _, page_df in train_df.groupby("Image", sort=False):
        page_samples, page_labels = main._column_boundary_samples(page_df)
        samples.extend(page_samples)
        labels.extend(page_labels)
    return main._fit_boundary_model(book_id, "column_boundary_model", samples, labels)


def test_fit_boundary_model_includes_training_metrics(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path

    model = _train_column_model("book1")

    assert model["available"] is True
    metrics = model.get("metrics")
    assert isinstance(metrics, dict)
    assert metrics["accuracy"] >= 0.9
    assert metrics["precision"] >= 0.9
    assert metrics["recall"] >= 0.9
    assert metrics["f1"] >= 0.9


def test_build_book_model_status_includes_feedback_summary(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path

    model = _train_column_model("book1")
    main._save_model_payload("book1", "column_boundary_model", model)
    main._record_feedback_event(
        "book1",
        {
            "task_type": "column",
            "source": "column_boundary_model",
            "accepted_count": 5,
            "corrected_count": 2,
            "total_count": 7,
            "page_id": "page_001",
        },
    )
    main._record_feedback_event(
        "book1",
        {
            "task_type": "column",
            "source": "column_boundary_model",
            "accepted_count": 3,
            "corrected_count": 1,
            "total_count": 4,
            "page_id": "page_002",
        },
    )

    status = main.build_book_model_status("book1")

    column_model = status["column_model"]
    feedback_summary = column_model["feedback_summary"]
    assert column_model["available"] is True
    assert feedback_summary["event_count"] == 2
    assert feedback_summary["accepted_count"] == 8
    assert feedback_summary["corrected_count"] == 3
    assert feedback_summary["acceptance_rate"] == 0.7273
    assert feedback_summary["last_page_id"] == "page_002"


def test_build_book_model_status_reports_global_fallback(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path

    global_model = _train_column_model(main.GLOBAL_MODEL_BOOK_ID)
    main._save_model_payload(main.GLOBAL_MODEL_BOOK_ID, "column_boundary_model", global_model)

    status = main.build_book_model_status("book1")

    assert status["column_model"]["available"] is False
    assert status["global_column_model"]["available"] is True
    assert status["effective_column_source"] == "global"


def test_collect_global_training_samples_uses_cached_book_samples(tmp_path: Path) -> None:
    main.OUTPUT_MODEL_DIR = tmp_path
    original_get_book_dirs = main.get_book_dirs

    samples = [
        {"dx": 0.1, "dy": 0.2, "upward": 0.0, "x_overlap": 0.9, "y_overlap": 0.1},
        {"dx": 0.8, "dy": 0.3, "upward": 1.0, "x_overlap": 0.1, "y_overlap": 0.2},
    ]
    labels = [0, 1]
    main._save_book_training_samples_cache("book_a", samples, labels, [], [])
    main._save_book_training_samples_cache("book_b", samples, labels, [], [])

    try:
        main.get_book_dirs = lambda: ["book_a", "book_b"]
        (
            column_samples,
            column_labels,
            segment_samples,
            segment_labels,
        ) = main._collect_global_training_samples()
    finally:
        main.get_book_dirs = original_get_book_dirs

    assert len(column_samples) == 4
    assert column_labels == [0, 1, 0, 1]
    assert segment_samples == []
    assert segment_labels == []
