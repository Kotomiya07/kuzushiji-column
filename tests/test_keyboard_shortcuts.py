from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shift_arrow_right_skips_without_saving() -> None:
    app_js = (ROOT / "static" / "app.js").read_text()

    assert "if (e.shiftKey) {\n                skipNextPage();" in app_js
    assert "} else {\n                navigatePage(1);" in app_js


def test_skip_shortcut_is_documented_in_ui_and_readme() -> None:
    template = (ROOT / "templates" / "index.html").read_text()
    readme = (ROOT / "README.md").read_text()

    assert "保存せず次のページへスキップ (Shift+→)" in template
    assert "<kbd>Shift</kbd> + <kbd>→</kbd>" in template
    assert "| Shift + → | 保存せず次のページへスキップ |" in readme


def test_select_focus_is_released_after_book_or_page_selection() -> None:
    app_js = (ROOT / "static" / "app.js").read_text()

    assert "elements.bookSelect.blur();" in app_js
    assert "elements.pageSelect.blur();" in app_js


def test_shortcuts_are_blocked_only_during_conflicting_interactions() -> None:
    app_js = (ROOT / "static" / "app.js").read_text()

    assert "e.ctrlKey || e.metaKey || e.altKey" in app_js
    assert "document.querySelector('dialog[open]')" in app_js
    assert "input, select, textarea, [contenteditable]" in app_js
