import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from httpx import Response
from fastapi.testclient import TestClient
from PIL import Image

sys.path.append(str(Path(__file__).resolve().parents[1]))

import main

COORDS = ["Unicode", "Image", "X", "Y", "Block ID", "Char ID", "Width", "Height"]
ANNOTATIONS = ["Column ID", *main.COLUMN_BOX_COLUMNS, "Wari ID", "Wari Part"]
BOOK = "book1"
PAGE = "page1"
API = f"/api/kcr-import/books/{BOOK}/pages/{PAGE}"
BOX = [302, 111, 30, 30]
KEYS = [
    f"{BOOK}:{PAGE}:{box}" for box in ("301,65,30,30", "302,111,30,30", "75,37,30,30")
]


def write_rows(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def coordinate(
    char_id: int, unicode: str, x: int, y: int, page: str = PAGE
) -> dict[str, str]:
    return dict(
        zip(
            COORDS,
            [unicode, page, str(x), str(y), "B0002", f"C{char_id:04d}", "30", "30"],
        )
    )


@dataclass
class Dataset:
    raw: Path
    mirror: Path
    output: Path
    segment: Path
    log: Path
    backups: Path

    def annotations(self, *, other_page: bool = False) -> None:
        rows = read_rows(self.raw)
        annotated: list[dict[str, str]] = []
        for i, row in enumerate(rows[:-1]):
            annotated.append(
                {
                    **row,
                    "Image": "other" if other_page else PAGE,
                    "Column ID": "COL0001" if i < 5 else "COL0002",
                    "Column X": "280" if i < 5 else "60",
                    "Column Y": "0",
                    "Column Width": "100",
                    "Column Height": "350",
                    "Wari ID": "W0001" if 1 <= i <= 3 else "",
                    "Wari Part": {1: "right", 2: "right", 3: "left"}.get(i, ""),
                }
            )
        write_rows(self.output, COORDS + ANNOTATIONS, annotated)
        write_rows(
            self.segment,
            COORDS + ANNOTATIONS + ["Segment ID"],
            [
                {**row, "Segment ID": f"SEG{i:04d}"}
                for i, row in enumerate(annotated, 1)
            ],
        )


@pytest.fixture
def dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Dataset:
    for name in ("RAW_DIR", "RAW_CSV_DIR", "OUTPUT_DIR", "OUTPUT_SEG_DIR"):
        monkeypatch.setattr(main, name, tmp_path / name.lower())
    monkeypatch.setattr(
        main.kcr_import, "CANDIDATES_PATH", tmp_path / "candidates.json"
    )
    monkeypatch.setattr(main.kcr_import, "DECISIONS_PATH", tmp_path / "decisions.jsonl")
    monkeypatch.setattr(main.kcr_import, "BACKUPS_DIR", tmp_path / "backups")
    raw = main.RAW_DIR / BOOK / f"{BOOK}_coordinate.csv"
    mirror = main.RAW_CSV_DIR / f"{BOOK}_coordinate.csv"
    output = main.OUTPUT_DIR / BOOK / "column_annotation.csv"
    segment = main.OUTPUT_SEG_DIR / BOOK / "column_annotation.csv"
    rows = [
        coordinate(1, "U+3042", 300, 20),
        coordinate(2, "U+E00A", 300, 110),
        coordinate(3, "U+3046", 300, 190),
        coordinate(4, "U+3048", 280, 115),
        coordinate(5, "U+304A", 300, 250),
        coordinate(6, "U+304B", 75, 20),
        coordinate(1, "U+304D", 75, 20, "other"),
    ]
    write_rows(raw, COORDS, rows)
    write_rows(mirror, COORDS, [rows[0], *rows[2:]])  # ミラーの旧データは正本と異なる
    images = raw.parent / "images"
    images.mkdir()
    for page in (PAGE, "empty"):
        Image.new("RGB", (450, 400), "white").save(images / f"{page}.jpg")

    common = {"book": BOOK, "split": "train", "in_corrected_list": True}
    match = {
        "char_id": "C0002",
        "unicode": "U+E00A",
        "box": [300, 110, 30, 30],
        "user_added": True,
    }
    near = {
        "char_id": "C0006",
        "unicode": "U+304B",
        "box": [75, 20, 30, 30],
        "user_added": False,
    }
    candidates = [
        {
            **common,
            "page": PAGE,
            "box": [301, 65, 30, 30],
            "status": "new",
            "match_iou": 0,
            "match": None,
        },
        {
            **common,
            "page": PAGE,
            "box": BOX,
            "status": "conflict_user",
            "match_iou": 0.82,
            "match": match,
        },
        {
            **common,
            "page": PAGE,
            "box": [75, 37, 30, 30],
            "status": "near",
            "match_iou": 0.35,
            "match": near,
        },
        {
            **common,
            "page": "empty",
            "box": [290, 80, 30, 30],
            "status": "new",
            "match_iou": 0,
            "match": None,
        },
    ]
    main.kcr_import.CANDIDATES_PATH.write_text(json.dumps(candidates), encoding="utf-8")
    return Dataset(
        raw, mirror, output, segment, tmp_path / "decisions.jsonl", tmp_path / "backups"
    )


def decision(
    index: int, value: str, unicode: str | None = None
) -> dict[str, str | None]:
    return {"candidate_id": KEYS[index], "decision": value, "unicode": unicode}


def save(*entries: dict[str, str | None]) -> Response:
    return TestClient(main.app).post(f"{API}/save", json={"decisions": entries})


def test_page_list_detail_and_statuses(dataset: Dataset) -> None:
    client = TestClient(main.app)
    data = client.get("/api/kcr-import/pages").json()
    assert data["summary"] == {
        "total_candidates": 4,
        "decided_count": 0,
        "undecided_count": 4,
        "total_pages": 2,
        "undecided_pages": 2,
    }
    page = next(item for item in data["pages"] if item["page_id"] == PAGE)
    assert page["total_count"] == page["undecided_count"] == 3
    assert page["status_counts"] == {"new": 1, "conflict_user": 1, "near": 1}
    response = client.get(API)
    assert response.status_code == 200, response.text
    detail = response.json()
    assert len(detail["characters"]) == 6
    assert (detail["image_width"], detail["image_height"]) == (450, 400)
    candidates = {item["status"]: item for item in detail["candidates"]}
    assert candidates["new"]["default_decision"] is None
    assert candidates["conflict_user"]["default_decision"] == "keep_existing"
    assert candidates["conflict_user"]["match"]["unicode"] == "U+E00A"
    assert candidates["conflict_user"]["resolved_match_char_id"] == "C0002"
    assert candidates["near"]["resolved_match_char_id"] == "C0006"


def test_partial_log_only_save_and_reload(dataset: Dataset) -> None:
    files = {p: p.read_bytes() for p in (dataset.raw, dataset.mirror)}
    response = save(decision(0, "reject"), decision(1, "keep_existing"))
    assert response.status_code == 200, response.text
    assert response.json()["remaining_undecided_count"] == 1
    assert response.json()["saved_count"] == 2
    assert not dataset.backups.exists()
    assert all(p.read_bytes() == before for p, before in files.items())
    logs = [json.loads(line) for line in dataset.log.read_text().splitlines()]
    assert len(logs) == 2
    assert datetime.fromisoformat(logs[1].pop("timestamp")).tzinfo is not None
    assert logs[1] == {
        "book": BOOK,
        "page": PAGE,
        "box": BOX,
        "status": "conflict_user",
        "decision": "keep_existing",
        "unicode": None,
        "target_char_id": "C0002",
    }
    with TestClient(main.app) as restarted:
        assert [
            item["candidate_id"] for item in restarted.get(API).json()["candidates"]
        ] == [KEYS[2]]
    assert save(decision(1, "keep_existing")).status_code == 409
    assert save(decision(2, "reject")).status_code == 200
    assert len(dataset.log.read_text().splitlines()) == 3
    pages = (
        TestClient(main.app)
        .get("/api/kcr-import/pages?only_undecided=true")
        .json()["pages"]
    )
    assert [page["page_id"] for page in pages] == ["empty"]


def test_empty_page_can_be_read_and_saved(dataset: Dataset) -> None:
    client = TestClient(main.app)
    url = f"/api/kcr-import/books/{BOOK}/pages/empty"
    assert client.get(url).json()["characters"] == []
    result = client.post(
        f"{url}/save",
        json={
            "decisions": [
                {
                    "candidate_id": f"{BOOK}:empty:290,80,30,30",
                    "decision": "add",
                    "unicode": "あ",
                }
            ]
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["remaining_undecided_count"] == 0
    assert read_rows(dataset.raw)[-1] == coordinate(1, "U+3042", 290, 80, "empty")
    assert json.loads(dataset.log.read_text())["unicode"] == "U+3042"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("U+3042", "U+3042"),
        ("u+3042", "U+3042"),
        ("あ", "U+3042"),
        ("𠀋", "U+2000B"),
    ],
)
def test_normalize_unicode(dataset: Dataset, value: str, expected: str) -> None:
    assert main.kcr_import.normalize_unicode_input(value) == expected


@pytest.mark.parametrize(
    ("index", "action"), [(0, "add"), (2, "add"), (1, "keep_both")]
)
@pytest.mark.parametrize(
    "value", [None, "", "  ", "あい", "U+GGGG", "U+D800", "U+110000"]
)
def test_invalid_unicode_rejects_entire_batch(
    dataset: Dataset, index: int, action: str, value: str | None
) -> None:
    other = decision(1, "keep_existing") if index != 1 else decision(0, "reject")
    before = dataset.raw.read_bytes(), dataset.mirror.read_bytes()
    assert save(other, decision(index, action, value)).status_code == 400
    assert before == (dataset.raw.read_bytes(), dataset.mirror.read_bytes())
    assert not dataset.log.exists()
    assert not dataset.backups.exists()


def test_replace_only_changes_coordinates_and_backs_up_all_targets(
    dataset: Dataset,
) -> None:
    dataset.annotations()
    paths = {
        "raw": dataset.raw,
        "raw_csv": dataset.mirror,
        "output": dataset.output,
        "output_seg": dataset.segment,
    }
    originals = {
        f"{kind}_{path.name}": path.read_bytes() for kind, path in paths.items()
    }
    expected = {p: read_rows(p) for p in (dataset.raw, dataset.output, dataset.segment)}
    response = save(decision(1, "replace"))
    assert response.status_code == 200, response.text
    assert response.json()["replaced_count"] == 1
    for path, rows in expected.items():
        rows[1].update(zip(("X", "Y", "Width", "Height"), map(str, BOX)))
        assert read_rows(path) == rows
    assert dataset.raw.read_bytes() == dataset.mirror.read_bytes()
    (folder,) = dataset.backups.iterdir()
    stamp = folder.name.removesuffix(f"_{BOOK}_{PAGE}")
    datetime.strptime(stamp, "%Y%m%d_%H%M%S_%f")
    assert {p.name: p.read_bytes() for p in folder.iterdir()} == originals
    assert json.loads(dataset.log.read_text())["target_char_id"] == "C0002"


@pytest.mark.parametrize("match_exists", [True, False])
def test_replace_does_not_trust_stale_char_id(
    dataset: Dataset, match_exists: bool
) -> None:
    rows = read_rows(dataset.raw)
    rows[1]["Char ID"] = "C0099"
    rows[2]["Char ID"] = "C0002"
    if not match_exists:
        rows[1]["X"] = "10"
    write_rows(dataset.raw, COORDS, rows)
    before = dataset.raw.read_bytes()
    response = save(decision(1, "replace"))
    if match_exists:
        assert response.status_code == 200, response.text
        updated = {row["Char ID"]: row for row in read_rows(dataset.raw)[:-1]}
        assert updated["C0002"] == rows[2]
        assert updated["C0099"]["X"] == "302"
        assert json.loads(dataset.log.read_text())["target_char_id"] == "C0099"
    else:
        assert response.status_code == 409
        assert dataset.raw.read_bytes() == before
        assert not dataset.log.exists()


def test_keep_both_retains_existing_box(dataset: Dataset) -> None:
    before = read_rows(dataset.raw)
    response = save(decision(1, "keep_both", "u+4e00"))
    assert response.status_code == 200, response.text
    after = read_rows(dataset.raw)
    assert len(after) == len(before) + 1
    assert [row["X"] for row in after if row["Unicode"] == "U+E00A"] == ["300"]
    assert [row["X"] for row in after if row["Unicode"] == "U+4E00"] == ["302"]


def test_renumber_preserves_columns_wari_and_segments(dataset: Dataset) -> None:
    dataset.annotations()
    assert PAGE in main.get_completed_pages(BOOK)
    old_output = read_rows(dataset.output)
    old_segment = read_rows(dataset.segment)
    # 派生側の古い Char ID が別文字を指していても属性は座標と Unicode で照合する。
    old_segment[1]["Char ID"], old_segment[4]["Char ID"] = (
        old_segment[4]["Char ID"],
        old_segment[1]["Char ID"],
    )
    write_rows(dataset.segment, COORDS + ANNOTATIONS + ["Segment ID"], old_segment)
    result = save(decision(0, "add", "一"))
    assert result.status_code == 200, result.text
    mapping = result.json()["char_id_map"]
    assert mapping == {
        "C0001": "C0001",
        **{f"C{i:04d}": f"C{i + 1:04d}" for i in range(2, 7)},
    }
    for path, old in ((dataset.output, old_output), (dataset.segment, old_segment)):
        rows = read_rows(path)
        assert [row["Char ID"] for row in rows] == [f"C{i:04d}" for i in range(1, 8)]
        assert rows[1]["Unicode"] == "U+4E00"
        attrs = [field for field in rows[0] if field not in COORDS]
        assert all(rows[1][field] == "" for field in attrs)
        assert [row["Wari Part"] for row in rows if row["Wari ID"]] == [
            "right",
            "right",
            "left",
        ]
        before = {row["Unicode"]: tuple(row[key] for key in attrs) for row in old}
        assert {
            row["Unicode"]: tuple(row[key] for key in attrs)
            for row in rows
            if row is not rows[1]
        } == before
    assert PAGE not in main.get_completed_pages(BOOK)
    page = TestClient(main.app).get(f"/api/books/{BOOK}/pages/{PAGE}").json()
    existing = next(char for char in page["characters"] if char["unicode"] == "U+E00A")
    assert (
        existing["char_id"],
        existing["column_id"],
        existing["wari_id"],
        existing["wari_part"],
    ) == (
        "C0003",
        "COL0001",
        "W0001",
        "right",
    )


@pytest.mark.parametrize("has_other_output", [False, True])
def test_x_order_and_output_does_not_gain_unsaved_pages(
    dataset: Dataset, has_other_output: bool
) -> None:
    if has_other_output:
        dataset.annotations(other_page=True)
    derivative = {
        p: p.read_bytes() for p in (dataset.output, dataset.segment) if p.exists()
    }
    unrelated = read_rows(dataset.raw)[-1]
    result = save(decision(0, "add", "一"), decision(2, "add", "二"))
    assert result.status_code == 200, result.text
    rows = read_rows(dataset.raw)
    assert [row["Unicode"] for row in rows[:-1]] == [
        "U+3042",
        "U+4E00",
        "U+E00A",
        "U+3048",
        "U+3046",
        "U+304A",
        "U+304B",
        "U+4E8C",
    ]
    assert rows[-1] == unrelated
    assert dataset.raw.read_bytes() == dataset.mirror.read_bytes()
    assert {
        p: p.read_bytes() for p in (dataset.output, dataset.segment) if p.exists()
    } == derivative
    assert len(dataset.log.read_text().splitlines()) == 2


def test_review_route_and_home_link() -> None:
    client = TestClient(main.app)
    review = client.get("/kcr-import")
    assert review.status_code == 200
    assert "KCR取り込み確認" in review.text
    assert "/static/kcr_import.js" in review.text
    home = client.get("/")
    assert home.status_code == 200
    assert 'href="/kcr-import"' in home.text


def test_log_failure_restores_coordinates(
    dataset: Dataset,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset.annotations()
    assert save(decision(2, "reject")).status_code == 200
    paths = (dataset.raw, dataset.mirror, dataset.output, dataset.segment, dataset.log)
    originals = {path: path.read_bytes() for path in paths}
    original_replace = Path.replace

    def fail_log_replace(temporary: Path, destination: Path) -> Path:
        if destination == dataset.log:
            raise OSError("simulated log write failure")
        return original_replace(temporary, destination)

    with monkeypatch.context() as fault:
        fault.setattr(Path, "replace", fail_log_replace)
        client = TestClient(main.app, raise_server_exceptions=False)
        response = client.post(
            f"{API}/save", json={"decisions": [decision(0, "add", "一")]}
        )
        assert response.status_code == 500
    assert {path: path.read_bytes() for path in paths} == originals
    assert save(decision(0, "add", "一")).status_code == 200
