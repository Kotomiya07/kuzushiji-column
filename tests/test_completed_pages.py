"""ページ完了条件（全文字に列あり＋全列に明示ボックスあり）のテスト。

サーバー: get_completed_pages / GET /api/books/{id}/pages / GET /api/books
クライアント: static/app.js の関数を Node.js で実行して検証する。
"""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi import BackgroundTasks
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT))

import main

IMAGE_SIZE = (400, 300)
PAGES = ("full", "unassigned", "partial", "nobox", "unsaved")
BOX1 = main.ColumnBoxData(x=290, y=40, width=80, height=70)
BOX2 = main.ColumnBoxData(x=190, y=40, width=80, height=70)


def _setup_book(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(main, "RAW_DIR", raw_dir)
    monkeypatch.setattr(main, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(main, "OUTPUT_SEG_DIR", tmp_path / "output_seg")

    book_dir = raw_dir / "book1"
    (book_dir / "images").mkdir(parents=True, exist_ok=True)
    rows = ["Image,Unicode,X,Y,Width,Height,Char ID,Block ID"]
    for page_id in PAGES:
        Image.new("RGB", IMAGE_SIZE, (240, 240, 230)).save(
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


def _save(page_id: str, columns: list[main.ColumnData]) -> None:
    request = main.SaveRequest(book_id="book1", page_id=page_id, columns=columns)
    asyncio.run(main.save_annotations("book1", page_id, request, BackgroundTasks()))


def _columns(
    box1: main.ColumnBoxData | None,
    box2: main.ColumnBoxData | None,
    ids1: tuple[str, ...] = ("C0001", "C0002"),
    ids2: tuple[str, ...] = ("C0003", "C0004"),
) -> list[main.ColumnData]:
    return [
        main.ColumnData(char_ids=list(ids1), box=box1),
        main.ColumnData(char_ids=list(ids2), box=box2),
    ]


def _save_scenarios() -> None:
    _save("full", _columns(BOX1, BOX2))
    # C0004 が未割当（保存CSVには Column ID 空の行として残る）
    _save("unassigned", _columns(BOX1, BOX2, ids2=("C0003",)))
    _save("partial", _columns(BOX1, None))
    _save("nobox", _columns(None, None))


# ===== サーバー: get_completed_pages / API =====
def test_only_fully_assigned_and_fully_boxed_page_is_completed(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save_scenarios()

    assert main.get_completed_pages("book1") == {"full"}


def test_pages_api_returns_completed_pages_subset_of_annotated(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save_scenarios()

    data = asyncio.run(main.get_pages("book1"))

    # annotated_pages / last_annotated は従来どおり（保存済みの全ページ）
    assert data["annotated_pages"] == ["full", "nobox", "partial", "unassigned"]
    assert data["last_annotated"] == "unassigned"
    # (1) 全割当＋全列ボックスは入る
    assert data["completed_pages"] == ["full"]
    assert set(data["completed_pages"]) <= set(data["annotated_pages"])
    # (2) 未割当文字あり (3) 一部の列のみボックス (4) ボックスなし (5) 未保存 は入らない
    for page_id in ("unassigned", "partial", "nobox", "unsaved"):
        assert page_id not in data["completed_pages"]


def test_books_api_returns_completed_count_and_keeps_annotated_count(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save_scenarios()

    books = asyncio.run(main.get_books())["books"]

    assert len(books) == 1
    book = books[0]
    assert book["total_pages"] == len(PAGES)
    assert book["annotated_count"] == 4
    assert book["completed_count"] == 1
    assert book["progress"] == round(1 / len(PAGES) * 100)


def test_resave_with_missing_box_removes_page_from_completed(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save("full", _columns(BOX1, BOX2))
    assert main.get_completed_pages("book1") == {"full"}

    _save("full", _columns(BOX1, None))

    assert main.get_completed_pages("book1") == set()


def test_resave_with_all_boxes_completes_previously_incomplete_page(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save("partial", _columns(BOX1, None))
    assert main.get_completed_pages("book1") == set()

    _save("partial", _columns(BOX1, BOX2))

    assert main.get_completed_pages("book1") == {"partial"}


def test_no_saved_csv_means_no_completed_pages(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)

    assert main.get_completed_pages("book1") == set()
    data = asyncio.run(main.get_pages("book1"))
    assert data["completed_pages"] == []
    assert asyncio.run(main.get_books())["books"][0]["completed_count"] == 0


def test_legacy_csv_without_box_columns_has_no_completed_pages(
    tmp_path, monkeypatch
) -> None:
    _setup_book(tmp_path, monkeypatch)
    output_dir = tmp_path / "output" / "book1"
    output_dir.mkdir(parents=True)
    (output_dir / "column_annotation.csv").write_text(
        "Unicode,Image,X,Y,Width,Height,Char ID,Block ID,Column ID\n"
        "U+4E00,full,300,50,20,20,C0001,,COL0001\n"
        "U+4E8C,full,300,80,20,20,C0002,,COL0001\n"
        "U+4E09,full,200,50,20,20,C0003,,COL0002\n"
        "U+56DB,full,200,80,20,20,C0004,,COL0002\n",
        encoding="utf-8",
    )

    assert main.get_completed_pages("book1") == set()


def test_saved_csv_missing_character_rows_is_not_completed(
    tmp_path, monkeypatch
) -> None:
    # 保存CSVに全文字行が無い（割当済みの文字行しかない）形式でも、
    # 元データの文字数と一致しなければ完了にしない
    _setup_book(tmp_path, monkeypatch)
    _save("full", _columns(BOX1, BOX2))
    csv_path = tmp_path / "output" / "book1" / "column_annotation.csv"
    df = pd.read_csv(csv_path)
    df[df["Char ID"] != "C0004"].to_csv(csv_path, index=False)

    assert main.get_completed_pages("book1") == set()


def test_blank_column_id_row_is_not_completed(tmp_path, monkeypatch) -> None:
    _setup_book(tmp_path, monkeypatch)
    _save("full", _columns(BOX1, BOX2))
    csv_path = tmp_path / "output" / "book1" / "column_annotation.csv"
    df = pd.read_csv(csv_path)
    df.loc[df["Char ID"] == "C0004", "Column ID"] = None
    df.to_csv(csv_path, index=False)

    assert main.get_completed_pages("book1") == set()


# ===== クライアント (static/app.js) =====
_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('static/app.js', 'utf8');

function extract(name) {
  const start = src.search(new RegExp('^(async )?function ' + name + '\\(', 'm'));
  if (start < 0) throw new Error('function not found: ' + name);
  let i = src.indexOf(') {', start) + 2;
  let depth = 0;
  for (; i < src.length; i++) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error('unterminated function: ' + name);
}

function load(names, globals) {
  const ctx = vm.createContext(Object.assign({ console }, globals));
  vm.runInContext(names.map(extract).join('\n'), ctx);
  return ctx;
}

function makePageSelect() {
  const select = { options: [], value: '' };
  Object.defineProperty(select, 'innerHTML', {
    // 実コードは innerHTML にプレースホルダ option を入れてから各ページを追加する
    set() { select.options = [{ value: '', textContent: 'ページを選択...' }]; },
    get() { return ''; },
  });
  select.appendChild = (option) => select.options.push(option);
  return select;
}

const document = { createElement: () => ({ value: '', textContent: '' }) };
const box = (x) => ({ x, y: 1, width: 10, height: 10 });
"""


def _run_node(body: str) -> Any:
    result = subprocess.run(
        ["node", "-"],
        input=_HARNESS + body,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_client_check_mark_uses_completed_pages_not_annotated_pages() -> None:
    out = _run_node(
        r"""
const pageSelect = makePageSelect();
const state = {
  pages: ['p1', 'p2', 'p3'],
  annotatedPages: ['p1', 'p2'],
  completedPages: ['p1'],
};
const ctx = load(['renderPageOptions'], { state, elements: { pageSelect }, document });
ctx.renderPageOptions('p3');
console.log(JSON.stringify(pageSelect.options.map((o) => o.textContent)));
"""
    )

    assert out[0] == "ページを選択..."
    assert out[1].startswith("p1") and "✅" in out[1]
    assert out[2] == "p2"
    assert out[3] == "p3"


def test_client_progress_uses_completed_count() -> None:
    out = _run_node(
        r"""
const ctx = load(
  ['formatBookOptionLabel', 'formatPageProgress', 'getDatasetProgress'],
  { state: { books: [] } },
);
const books = [
  { book_id: 'A', total_pages: 10, annotated_count: 8, completed_count: 2, progress: 20 },
  { book_id: 'B', total_pages: 10, annotated_count: 5, completed_count: 3, progress: 30 },
];
const dataset = ctx.getDatasetProgress(books);
console.log(JSON.stringify({
  label: ctx.formatBookOptionLabel(books[0]),
  progress: ctx.formatPageProgress(books[0]),
  dataset,
  datasetText: ctx.formatPageProgress(dataset),
}));
"""
    )

    assert out["label"] == "A (2/10ページ, 20%)"
    assert out["progress"] == "2/10(20%)"
    assert out["dataset"]["completed_count"] == 5
    assert out["dataset"]["total_pages"] == 20
    assert out["dataset"]["progress"] == 25
    assert out["datasetText"] == "5/20(25%)"


def test_client_load_pages_imports_completed_pages() -> None:
    out = _run_node(
        r"""
const pageSelect = makePageSelect();
const state = { bookId: 'b', pages: [], annotatedPages: [], completedPages: [], lastAnnotated: null };
const globals = {
  state,
  elements: { pageSelect },
  document,
  clearImageCache: () => {},
  fetch: async () => ({
    json: async () => ({
      pages: ['p1', 'p2'],
      annotated_pages: ['p1', 'p2'],
      completed_pages: ['p2'],
      last_annotated: 'p2',
    }),
  }),
};
const ctx = load(['loadPages', 'renderPageOptions'], globals);
ctx.loadPages('b').then(() => console.log(JSON.stringify({
  annotated: state.annotatedPages,
  completed: state.completedPages,
  labels: pageSelect.options.map((o) => o.textContent),
})));
"""
    )

    assert out["annotated"] == ["p1", "p2"]
    assert out["completed"] == ["p2"]
    assert out["labels"][1] == "p1"
    assert "✅" in out["labels"][2]


_SAVE_STATUS_BODY = r"""
function run(snapshotOverrides, initialCompleted) {
  const pageSelect = makePageSelect();
  const state = {
    pages: ['p1', 'p2'],
    annotatedPages: [],
    completedPages: initialCompleted,
    lastAnnotated: null,
  };
  const ctx = load(
    ['updatePageSaveStatus', 'isSnapshotCompleted', 'getUnassignedCount', 'isValidColumnBox', 'renderPageOptions'],
    { state, elements: { pageSelect }, document },
  );
  const snapshot = Object.assign({
    pageId: 'p1',
    characters: [{}, {}, {}],
    columns: [[0, 1], [2]],
    columnBoxes: [box(1), box(20)],
  }, snapshotOverrides);
  ctx.updatePageSaveStatus(snapshot);
  return { annotated: state.annotatedPages, completed: state.completedPages };
}
"""


def test_client_save_marks_completed_only_when_assigned_and_all_boxed() -> None:
    out = _run_node(
        _SAVE_STATUS_BODY
        + r"""
console.log(JSON.stringify({
  complete: run({}, []),
  unassigned: run({ columns: [[0, 1]] }, []),
  partialBox: run({ columnBoxes: [box(1), null] }, []),
  noBox: run({ columnBoxes: [null, null] }, []),
  invalidBox: run({ columnBoxes: [box(1), { x: 1, y: 1, width: 0, height: 5 }] }, []),
}));
"""
    )

    assert out["complete"] == {"annotated": ["p1"], "completed": ["p1"]}
    # 未割当・一部ボックス・ボックスなし・不正ボックスは annotated には入るが completed には入らない
    for key in ("unassigned", "partialBox", "noBox", "invalidBox"):
        assert out[key] == {"annotated": ["p1"], "completed": []}


def test_client_save_removes_page_from_completed_when_no_longer_complete() -> None:
    out = _run_node(
        _SAVE_STATUS_BODY
        + r"""
console.log(JSON.stringify({
  removed: run({ columnBoxes: [box(1), null] }, ['p1', 'p2']),
  kept: run({}, ['p2']),
}));
"""
    )

    assert out["removed"]["completed"] == ["p2"]
    assert out["removed"]["annotated"] == ["p1"]
    assert out["kept"]["completed"] == ["p1", "p2"]


def test_save_snapshot_calls_updated_status_function() -> None:
    app_js = (ROOT / "static" / "app.js").read_text()

    assert "updatePageSaveStatus(snapshot)" in app_js
    assert "markPageAnnotated" not in app_js
