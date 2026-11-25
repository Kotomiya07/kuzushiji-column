"""割注UIのテキストアサーション（ブラウザなしで要素ID・関数・ドキュメントを検証する）。"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text()


def _function_body(source: str, name: str) -> str:
    """`function name(` から対応する閉じ括弧までの本体を返す。"""
    match = re.search(rf"(?:async )?function {name}\(", source)
    assert match, f"function {name} not found"
    # 引数のデフォルト値 `options = {}` を本体と取り違えないよう、閉じ括弧の後の `{` を探す
    body_open = re.compile(r"\)\s*\{").search(source, match.end())
    assert body_open, f"function {name} body not found"
    start = body_open.end() - 1
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"function {name} is not closed")


# ===== テンプレート =====
def test_wari_inspector_elements_exist() -> None:
    template = _read("templates", "index.html")

    for element_id in (
        "wari-panel",
        "wari-state",
        "btn-wari-range",
        "btn-wari-sep",
        "btn-wari-remove",
    ):
        assert f'id="{element_id}"' in template

    assert "割注" in template
    assert "範囲を開始/確定" in template
    assert "選択文字を境目にする" in template
    assert "選択文字の割注を解除" in template
    assert "割注なし" in template


def test_app_js_version_is_bumped_for_wari() -> None:
    template = _read("templates", "index.html")

    assert "/static/app.js?v=24" in template
    assert "/static/app.js?v=23" not in template


def test_help_dialog_documents_wari() -> None:
    template = _read("templates", "index.html")

    assert "<h3>割注</h3>" in template
    assert "<kbd>W</kbd>" in template
    assert "<kbd>Shift</kbd> + <kbd>W</kbd>" in template


# ===== app.js =====
def test_app_js_declares_wari_state_and_colors() -> None:
    script = _read("static", "app.js")

    assert "wariByChar: []" in script
    assert "wariPendingStart: null" in script
    assert "wari:" in script  # COLORS.wari


def test_app_js_defines_wari_operations() -> None:
    script = _read("static", "app.js")

    for name in (
        "restoreWariFromAnnotations",
        "toggleWariRange",
        "setWariSepAtSelection",
        "removeWariAtSelection",
        "pruneInvalidWariGroups",
        "estimateWariSplit",
        "updateWariPanel",
    ):
        assert re.search(rf"function {name}\(", script), name


def test_app_js_binds_wari_elements() -> None:
    script = _read("static", "app.js")

    assert "document.getElementById('wari-state')" in script
    assert "document.getElementById('btn-wari-range')" in script
    assert "document.getElementById('btn-wari-sep')" in script
    assert "document.getElementById('btn-wari-remove')" in script
    assert "elements.btnWariRange.addEventListener('click', toggleWariRange)" in script
    assert "elements.btnWariSep.addEventListener('click', setWariSepAtSelection)" in script
    assert "elements.btnWariRemove.addEventListener('click', removeWariAtSelection)" in script


def test_load_page_data_restores_wari_after_columns() -> None:
    body = _function_body(_read("static", "app.js"), "loadPageData")

    assert "restoreWariFromAnnotations()" in body
    assert body.index("restoreColumnsFromAnnotations()") < body.index("restoreWariFromAnnotations()")
    # 初期履歴にも割注を含める
    assert "wariByChar" in body


def test_wari_included_in_history_and_snapshot() -> None:
    script = _read("static", "app.js")

    for name in ("saveHistory", "undo", "redo", "createSaveSnapshot"):
        assert "wari" in _function_body(script, name).lower(), name


def test_save_payload_contains_wari() -> None:
    script = _read("static", "app.js")
    body = _function_body(script, "saveSnapshot")

    assert "wari:" in body
    assert "buildWariPayload(snapshot)" in body

    payload_body = _function_body(script, "buildWariPayload")
    assert "wari_id" in payload_body
    assert "wari_part" in payload_body
    assert "char_id" in payload_body


def test_prune_called_after_structure_changes() -> None:
    script = _read("static", "app.js")

    for name in ("splitColumn", "unassignCharacter", "clearAllAssignments", "autoEstimateColumns"):
        assert "pruneInvalidWariGroups()" in _function_body(script, name), name
    # 予測適用（applySuggestionsIfAvailable）でも整合性を保つ
    assert "pruneInvalidWariGroups()" in _function_body(script, "applySuggestionsIfAvailable")


def test_wari_edits_mark_dirty_and_are_column_mode_only() -> None:
    script = _read("static", "app.js")

    for name in ("toggleWariRange", "setWariSepAtSelection", "removeWariAtSelection"):
        body = _function_body(script, name)
        assert "getEffectiveEditMode() !== 'column'" in body, name

    commit = _function_body(script, "commitWariEdit")
    assert "state.isDirty = true" in commit
    assert "saveHistory()" in commit


def test_wari_split_maximises_right_minus_left_x_centre() -> None:
    body = _function_body(_read("static", "app.js"), "estimateWariSplit")

    assert "for (let k = 1; k < " in body
    assert "bestScore" in body


def test_keydown_handles_w_and_shift_w() -> None:
    body = _function_body(_read("static", "app.js"), "handleKeydown")

    assert "'w'" in body or '"w"' in body
    assert "toggleWariRange()" in body
    assert "setWariSepAtSelection()" in body
    assert "e.shiftKey" in body


def test_draw_renders_wari_in_column_view_with_dashed_rects() -> None:
    script = _read("static", "app.js")
    body = _function_body(script, "drawWari")

    assert "setLineDash" in body
    assert "wariPendingStart" in body
    assert "drawWari(" in _function_body(script, "drawCanvas")


# ===== CSS =====
def test_wari_panel_styles_exist() -> None:
    style = _read("static", "style.css")

    assert ".wari-panel" in style


# ===== README =====
def test_readme_documents_wari() -> None:
    readme = _read("README.md")

    assert "割注" in readme
    assert "Wari ID" in readme
    assert "Wari Part" in readme
    assert "Shift+W" in readme or "Shift + W" in readme
    assert "選択文字を境目にする" in readme
