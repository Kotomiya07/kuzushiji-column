"""列ボックスUIのテキストアサーション（ブラウザなしで要素ID・関数・ドキュメントを検証する）。"""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(*parts: str) -> str:
    return (ROOT.joinpath(*parts)).read_text()


# ===== テンプレート =====
def test_column_box_inspector_elements_exist() -> None:
    template = _read("templates", "index.html")

    for element_id in (
        "column-box-extend-px",
        "btn-extend-selected-column",
        "btn-extend-all-columns",
        "btn-reset-selected-column-box",
        "btn-reset-column-boxes",
        "column-box-x",
        "column-box-y",
        "column-box-width",
        "column-box-height",
        "btn-apply-column-box",
    ):
        assert f'id="{element_id}"' in template

    assert "列ボックス" in template
    assert "右側に追加 (px)" in template
    assert "選択列を広げる" in template
    assert "全列を広げる" in template
    assert "全列を自動に戻す" in template
    assert "選択列を自動に戻す" in template
    # 列確定時の自動拡張は px 設定だけで効くため、チェックボックスは置かない
    assert "列確定時に自動で広げる" not in template
    assert "column-box-auto-expand" not in template


def test_extend_px_default_is_40() -> None:
    template = _read("templates", "index.html")

    assert 'id="column-box-extend-px"' in template
    assert 'value="40"' in template


def test_app_js_version_is_bumped() -> None:
    template = _read("templates", "index.html")

    assert "/static/app.js?v=24" in template
    assert "/static/app.js?v=23" not in template


def test_help_dialog_documents_column_box() -> None:
    template = _read("templates", "index.html")

    assert "<kbd>E</kbd>" in template
    assert "<kbd>Shift</kbd> + <kbd>E</kbd>" in template
    assert "<h3>列ボックス</h3>" in template


# ===== app.js =====
def test_app_js_defines_column_box_functions() -> None:
    app_js = _read("static", "app.js")

    for name in (
        "getEffectiveColumnBox",
        "extendColumnBox",
        "getBulkExtendVerticalPx",
        "extendSelectedColumnBox",
        "extendAllColumnBoxes",
        "resetSelectedColumnBox",
        "resetAllColumnBoxes",
        "applyColumnBoxInputs",
        "updateColumnBoxInputs",
        "applyAutoExtendToNewColumns",
        "hitTestColumnBoxEdge",
        "handleBoxPointerDown",
        "handleBoxPointerMove",
        "finishBoxDrag",
    ):
        assert f"function {name}(" in app_js


def test_reset_selected_column_box_is_wired() -> None:
    """選択列を自動に戻すボタンの登録・リスナー・無効化・履歴確定が揃っている。"""
    app_js = _read("static", "app.js")

    assert "btnResetSelectedColumnBox: document.getElementById('btn-reset-selected-column-box')" in app_js
    assert "elements.btnResetSelectedColumnBox?.addEventListener('click', resetSelectedColumnBox)" in app_js
    # 無効化は updateColumnBoxInputs 内（列なしは常に無効、自動列は isAuto で無効）
    start = app_js.index("function updateColumnBoxInputs(")
    end = app_js.index("function applyColumnBoxInputs(")
    body = app_js[start:end]
    assert "elements.btnResetSelectedColumnBox.disabled = true" in body
    assert "elements.btnResetSelectedColumnBox.disabled = isAuto" in body
    # 本体は選択列だけを null にして commitColumnBoxEdit で確定する
    func_start = app_js.index("function resetSelectedColumnBox(")
    func_end = app_js.index("function resetAllColumnBoxes(")
    func = app_js[func_start:func_end]
    assert "state.columnBoxes[colIdx] = null" in func
    assert "commitColumnBoxEdit('選択列のボックスを自動に戻しました')" in func


def test_help_and_readme_document_reset_selected_column_box() -> None:
    template = _read("templates", "index.html")
    readme = _read("README.md")

    assert "「選択列を自動に戻す」" in template
    assert "「選択列を自動に戻す」" in readme


def test_app_js_syncs_column_boxes_with_history_and_save() -> None:
    app_js = _read("static", "app.js")

    assert "columnBoxes: []" in app_js
    assert "columnBoxes: JSON.parse(JSON.stringify(state.columnBoxes))" in app_js
    assert "restoreColumnBoxesFromSnapshot" in app_js
    assert "savedColumnBoxes" in app_js
    assert "column.box = {" in app_js


def test_app_js_persists_preferences_with_kuzushiji_prefix() -> None:
    app_js = _read("static", "app.js")

    assert "kuzushiji.columnBoxExtendPx" in app_js
    assert "kuzushiji.columnBoxExtendVerticalPx" in app_js
    # 自動拡張のチェックボックス廃止に伴い、保存キーも持たない
    assert "kuzushiji.columnBoxAutoExtend" not in app_js


def test_app_js_drag_uses_pointer_events_and_suppresses_click() -> None:
    app_js = _read("static", "app.js")

    assert "'pointerdown'" in app_js
    assert "'pointermove'" in app_js
    assert "'pointerup'" in app_js
    assert "ew-resize" in app_js
    assert "ns-resize" in app_js
    assert "suppressNextClick" in app_js
    assert "COLUMN_BOX_MIN_SIZE = 8" in app_js


def _extract_hit_test_function(app_js: str) -> str:
    """hitTestColumnBoxEdge 関数の本体テキストを切り出す。"""
    start = app_js.index("function hitTestColumnBoxEdge(")
    end = app_js.index("function getEdgeCursor(")
    return app_js[start:end]


def test_hit_test_targets_right_top_bottom_for_all_columns() -> None:
    """右・上・下辺は全列が対象で、左辺だけが選択列限定になっている。"""
    body = _extract_hit_test_function(_read("static", "app.js"))

    guard = "if (colIdx !== selectedColIdx) continue;"
    assert guard in body

    before_guard, after_guard = body.split(guard)
    # 選択列以外にも効く判定（ガードより前）
    assert "edge: 'right'" in before_guard
    assert "edge: 'top'" in before_guard
    assert "edge: 'bottom'" in before_guard
    # 選択列だけに効く判定（ガードより後ろ）
    assert "edge: 'left'" in after_guard
    assert "edge: 'right'" not in after_guard
    assert "edge: 'top'" not in after_guard
    assert "edge: 'bottom'" not in after_guard


def test_hit_test_comment_describes_new_edge_rule() -> None:
    app_js = _read("static", "app.js")

    assert "それ以外の列は右辺のみ" not in app_js
    assert "全列は右・上・下辺、選択列は左辺も" in app_js


def test_help_and_readme_describe_vertical_edge_drag_for_all_columns() -> None:
    template = _read("templates", "index.html")
    readme = _read("README.md")

    assert "それ以外の列は右辺をドラッグ" not in readme
    assert "それ以外の列は右辺をドラッグ" not in template
    assert "全列の右辺・上辺・下辺" in readme
    assert "全列の右辺・上辺・下辺" in template


def test_e_key_shortcut_follows_keydown_convention() -> None:
    app_js = _read("static", "app.js")

    assert "e.key.toLowerCase() === 'e'" in app_js
    assert "extendSelectedColumnBox(e.shiftKey ? -1 : 1)" in app_js
    # E の処理は修飾キー・入力中・ダイアログ中のガードより後ろにある
    assert app_js.index("if (isEditing || isDialogOpen || hasBrowserModifier) return;") < app_js.index(
        "e.key.toLowerCase() === 'e'"
    )


# ===== 上下 px 入力 =====
def _extract_function(app_js: str, name: str) -> str:
    """app.js からトップレベル関数 name の本体テキストを切り出す。"""
    start = app_js.index(f"function {name}(")
    end = app_js.find("\nfunction ", start + 1)
    return app_js[start:] if end < 0 else app_js[start:end]


def test_vertical_px_input_exists_with_default_zero() -> None:
    template = _read("templates", "index.html")

    assert 'id="column-box-extend-vertical-px"' in template
    assert "上下に追加 (px)" in template
    tag_start = template.index('id="column-box-extend-vertical-px"')
    tag = template[tag_start : template.index(">", tag_start)]
    assert 'min="0"' in tag
    assert 'value="0"' in tag
    # 「右側に追加 (px)」の隣（右側 px の入力より後ろ、操作ボタン群より前）にある
    assert template.index('id="column-box-extend-px"') < tag_start
    assert tag_start < template.index('id="btn-extend-selected-column"')


def test_vertical_px_is_persisted_and_normalized() -> None:
    app_js = _read("static", "app.js")

    assert "extendVerticalPx: 'kuzushiji.columnBoxExtendVerticalPx'" in app_js
    assert "columnBoxExtendVerticalPx: document.getElementById('column-box-extend-vertical-px')" in app_js
    assert "COLUMN_BOX_STORAGE_KEYS.extendVerticalPx" in app_js
    # 読み取りは右側 px と同じ流儀で正規化し、不正値は 0 にする
    getter = _extract_function(app_js, "getBulkExtendVerticalPx")
    assert "elements.columnBoxExtendVerticalPx" in getter
    assert ": 0" in getter
    # 復元と change リスナー
    load = _extract_function(app_js, "loadColumnBoxPreferences")
    assert "elements.columnBoxExtendVerticalPx" in load
    assert "COLUMN_BOX_STORAGE_KEYS.extendVerticalPx" in load
    setup = _extract_function(app_js, "setupColumnBoxControls")
    assert "elements.columnBoxExtendVerticalPx?.addEventListener('change'" in setup


def test_vertical_px_is_applied_on_every_extend_path() -> None:
    """E / Shift+E・全列拡張・列確定時の自動拡張のすべてで上下 px が効く。"""
    app_js = _read("static", "app.js")

    selected = _extract_function(app_js, "extendSelectedColumnBox")
    assert "getBulkExtendVerticalPx() * direction" in selected
    assert "extendColumnBox(colIdx, px, verticalPx)" in selected

    all_columns = _extract_function(app_js, "extendAllColumnBoxes")
    assert "getBulkExtendVerticalPx()" in all_columns
    assert "extendColumnBox(colIdx, px, verticalPx)" in all_columns

    auto = _extract_function(app_js, "applyAutoExtendToNewColumns")
    assert "getBulkExtendVerticalPx()" in auto
    assert "extendColumnBox(colIdx, px, verticalPx)" in auto


def test_extend_column_box_clamps_vertical_to_image_and_min_size() -> None:
    body = _extract_function(_read("static", "app.js"), "extendColumnBox")

    assert "verticalPx" in body
    # 上は 0、下は画像の高さで止まり、最小サイズを下回らない
    assert "Math.max(0, base.y - verticalPx)" in body
    assert "Math.min(state.imageHeight, base.y + base.height + verticalPx)" in body
    assert "COLUMN_BOX_MIN_SIZE" in body


def test_all_columns_toast_mentions_vertical_px_only_when_positive() -> None:
    body = _extract_function(_read("static", "app.js"), "extendAllColumnBoxes")

    assert "列の右側を${px}px、上下を各${verticalPx}px広げました" in body
    assert "列の右側を${px}px広げました" in body
    assert "verticalPx > 0" in body


def test_help_and_readme_describe_vertical_px() -> None:
    template = _read("templates", "index.html")
    readme = _read("README.md")

    assert "「上下に追加 (px)」" in template
    assert "「上下に追加 (px)」" in readme


# ===== G キー（全列を広げる） =====
def test_g_key_extends_all_columns_after_keydown_guards() -> None:
    app_js = _read("static", "app.js")

    assert "e.key.toLowerCase() === 'g'" in app_js
    guard = app_js.index("if (isEditing || isDialogOpen || hasBrowserModifier) return;")
    g_branch = app_js.index("e.key.toLowerCase() === 'g'")
    assert guard < g_branch
    # E の処理の直後に置き、同じ形で extendAllColumnBoxes() を呼ぶ
    assert app_js.index("e.key.toLowerCase() === 'e'") < g_branch
    branch_end = app_js.index("switch (e.key)", g_branch)
    assert "extendAllColumnBoxes();" in app_js[g_branch:branch_end]
    assert "e.preventDefault();" in app_js[g_branch:branch_end]


def test_extend_all_button_shows_g_kbd_hint() -> None:
    template = _read("templates", "index.html")

    button_start = template.index('id="btn-extend-all-columns"')
    button = template[button_start : template.index("</button>", button_start)]
    assert '<kbd class="btn__kbd">G</kbd>' in button
    assert "(G)" in button  # title にもキーを併記する


def test_help_and_readme_list_g_shortcut() -> None:
    template = _read("templates", "index.html")
    readme = _read("README.md")

    assert "<tr><td><kbd>G</kbd></td><td>全列のボックスを広げる" in template
    assert "| G | 全列のボックスを広げる" in readme
    # E / Shift+E の説明に上下 px も効くことが書かれている
    assert "上下に追加 px" in template
    assert "上下も同時に狭め" in template
    assert "上下に追加 (px)" in readme


# ===== CSS =====
def test_style_defines_column_box_panel() -> None:
    css = _read("static", "style.css")

    assert ".column-box-panel__input" in css
    assert ".column-box-panel__grid" in css


# ===== README =====
def test_readme_documents_column_box() -> None:
    readme = _read("README.md")

    assert "### 列ボックス" in readme
    assert "| E |" in readme
    assert "| Shift + E |" in readme
    for column in ("Column X", "Column Y", "Column Width", "Column Height"):
        assert column in readme


# ===== 読み込み時の自動拡張と次ページ自動保存 =====
def _function_body(app_js: str, name: str, next_name: str) -> str:
    """app.js から関数 name の本体（次の関数の直前まで）を取り出す。"""
    start = app_js.index(f"function {name}(")
    end = app_js.index(f"function {next_name}(", start)
    return app_js[start:end]


def test_auto_box_extend_function_and_flag_exist() -> None:
    app_js = _read("static", "app.js")

    assert "function autoExtendColumnBoxesOnLoad(" in app_js
    assert "function isAutoBoxExtendSaveCandidate(" in app_js
    assert "autoBoxExtended: false," in app_js


def test_auto_extend_on_load_conditions_and_guards() -> None:
    app_js = _read("static", "app.js")
    body = _function_body(app_js, "autoExtendColumnBoxesOnLoad", "resetSelectedColumnBox")

    # 列なし・調整済みボックスありのページでは行わない
    assert "state.columns.length === 0" in body
    assert "state.columnBoxes.some(Boolean)" in body
    # 右 px と上下 px が両方 0 のときは何もしない
    assert "px <= 0 && verticalPx <= 0" in body
    # 現在の px 設定を全列に適用し、変わった列が 0 なら何もしない
    assert "getBulkExtendPx()" in body
    assert "getBulkExtendVerticalPx()" in body
    assert "if (changed === 0) return;" in body
    # 自動適用の印を立てる。ユーザー編集（hasUserEdited）としては扱わない
    assert "state.autoBoxExtended = true" in body
    assert "state.isDirty = true" in body
    assert "markUserEdited" not in body
    assert "hasUserEdited = true" not in body
    assert "自動で全列のボックスを広げました" in body


def test_auto_extend_runs_after_load_state_is_settled() -> None:
    app_js = _read("static", "app.js")
    load = _function_body(app_js, "loadPageData", "getPageImageUrl")

    # ページ読み込みごとにリセットされ、履歴の初期状態の後で自動拡張される
    assert "state.autoBoxExtended = false;" in load
    assert load.index("state.history = [{") < load.index("autoExtendColumnBoxesOnLoad();")
    assert load.index("state.initialAutoGenerated = state.isDirty") < load.index("autoExtendColumnBoxesOnLoad();")


def test_auto_box_extended_is_synced_with_history() -> None:
    app_js = _read("static", "app.js")

    # スナップショットの保存（初期・saveHistory）と Undo/Redo の復元
    assert app_js.count("autoBoxExtended: state.autoBoxExtended,") == 2
    assert app_js.count("state.autoBoxExtended = Boolean(snapshot.autoBoxExtended);") == 2
    # ユーザー編集で意味が失われるので落とす
    mark = _function_body(app_js, "markUserEdited", "formatTimestamp")
    assert "state.autoBoxExtended = false" in mark


def test_navigate_page_auto_saves_after_auto_box_extend() -> None:
    app_js = _read("static", "app.js")

    cand = _function_body(app_js, "isAutoBoxExtendSaveCandidate", "showLoading")
    assert "!state.autoBoxExtended || state.hasUserEdited" in cand
    assert "if (!state.isDirty) return false;" in cand
    # isSegmentsDirty は、保存済みの列がないページ（自動生成ページ）でのみ候補から外す
    assert "state.isSegmentsDirty && !state.hasOriginalAnnotations" in cand
    assert "state.columns.length === 0" in cand
    assert "hasUnassignedCharacters()" in cand

    nav = _function_body(app_js, "navigatePage", "skipNextPage")
    # 前方移動のみ。既存の候補の判定は残したまま、追加の分岐として自動保存する
    assert "direction > 0 && isInitialAutoSaveCandidate()" in nav
    assert "direction > 0 && isAutoBoxExtendSaveCandidate()" in nav
    assert nav.index("isInitialAutoSaveCandidate()") < nav.index("isAutoBoxExtendSaveCandidate()")
    branch = nav[nav.index("isAutoBoxExtendSaveCandidate()"):]
    branch = branch[: branch.index("} else if")]
    assert "createSaveSnapshot()" in branch
    assert "startValidatedBackgroundSave(snapshot)" in branch
    assert "startedBackgroundSave = true" in branch
    # skipSave（保存せずスキップ）が最優先で、自動保存の対象外
    assert nav.index("options.skipSave") < nav.index("isAutoBoxExtendSaveCandidate()")


_NODE_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync('static/app.js', 'utf8');

function extract(name) {
  const m = new RegExp('(?:async )?function ' + name + '\\(').exec(src);
  if (!m) throw new Error('function not found: ' + name);
  let i = src.indexOf('{', src.indexOf(')', m.index));
  let depth = 0;
  for (let j = i; j < src.length; j++) {
    if (src[j] === '{') depth++;
    else if (src[j] === '}' && --depth === 0) return src.slice(m.index, j + 1);
  }
  throw new Error('unbalanced: ' + name);
}

function load(names, context) {
  vm.createContext(context);
  vm.runInContext(names.map(extract).join('\n'), context);
  return context;
}
"""


def _run_node(body: str) -> object:
    result = subprocess.run(
        ["node", "-e", _NODE_HARNESS + body],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_auto_box_extend_save_candidate_behavior() -> None:
    # 読み込み由来の isSegmentsDirty は、保存済みの列があるページでのみ不問にする
    out = _run_node(
        r"""
const base = {
  autoBoxExtended: true, hasUserEdited: false, isDirty: true, isSegmentsDirty: false,
  hasOriginalAnnotations: true, columns: [[0, 1]], characters: [{}, {}],
};
const ctx = load(['hasUnassignedCharacters', 'isAutoBoxExtendSaveCandidate'], { state: {} });
function check(overrides) {
  ctx.state = Object.assign({}, base, overrides);
  return vm.runInContext('isAutoBoxExtendSaveCandidate()', ctx);
}
console.log(JSON.stringify({
  baseline: check({}),
  originalSegDirty: check({ isSegmentsDirty: true }),
  newPageSegDirty: check({ hasOriginalAnnotations: false, isSegmentsDirty: true }),
  newPageSegClean: check({ hasOriginalAnnotations: false }),
  userEditedOriginal: check({ isSegmentsDirty: true, hasUserEdited: true }),
  userEditedNew: check({ hasOriginalAnnotations: false, isSegmentsDirty: true, hasUserEdited: true }),
  notAutoExtended: check({ isSegmentsDirty: true, autoBoxExtended: false }),
  notDirty: check({ isSegmentsDirty: true, isDirty: false }),
  noColumns: check({ isSegmentsDirty: true, columns: [] }),
  unassigned: check({ isSegmentsDirty: true, columns: [[0]] }),
}));
"""
    )

    # (1) hasOriginalAnnotations=true なら isSegmentsDirty=true でも候補になる
    assert out["originalSegDirty"] is True
    assert out["baseline"] is True
    # (2) hasOriginalAnnotations=false で isSegmentsDirty=true なら候補にならない
    assert out["newPageSegDirty"] is False
    assert out["newPageSegClean"] is True
    # (3) hasUserEdited=true なら従来どおり候補にならない
    assert out["userEditedOriginal"] is False
    assert out["userEditedNew"] is False
    # 他の条件は変更されていない
    assert out["notAutoExtended"] is False
    assert out["notDirty"] is False
    assert out["noColumns"] is False
    assert out["unassigned"] is False


def test_background_auto_save_saves_columns_and_segments() -> None:
    # 保存済みの列・未保存のセグメントがあるページを、自動保存と同じ経路で保存する
    out = _run_node(
        r"""
const calls = [];
const names = [
  'cloneForSave', 'createSaveSnapshot', 'getUnassignedCount', 'validateColumnSaveSnapshot',
  'validateSegmentSaveSnapshot', 'renumberSnapshotSegmentIdsIfAuto', 'buildSegmentsPayload',
  'buildWariPayload', 'saveSnapshot', 'getSaveSnapshotValidationError',
  'startValidatedBackgroundSave', 'startBackgroundSave',
];
const ctx = load(names, {
  state: {
    bookId: 'book1', pageId: 'page1',
    characters: [{ char_id: 'C1' }, { char_id: 'C2' }, { char_id: 'C3' }],
    columns: [[0, 1], [2]], columnIds: ['COL0001', 'COL0002'],
    columnBoxes: [{ x: 1, y: 2, width: 30, height: 40 }, null],
    wariByChar: [null, null, null],
    segments: [[0, 1]], segmentIds: ['SEG0001'],
    isDirty: true, isSegmentsDirty: true, hasOriginalAnnotations: true,
    hasCustomSegmentIds: false, predictionMeta: null,
  },
  buildColumnPredictionFeedbackFrom: () => null,
  buildSegmentPredictionFeedbackFrom: () => null,
  showLoading: () => {}, showToast: () => {}, showSaveValidationError: () => {},
  updatePageSaveStatus: () => {}, updateBookProgress: () => {}, scheduleModelStatusRefresh: () => {},
  console, setTimeout,
  fetch: async (url, init) => {
    calls.push({ url, body: JSON.parse(init.body) });
    return { ok: true, json: async () => ({ success: true, message: 'ok' }) };
  },
});
const snapshot = vm.runInContext('createSaveSnapshot()', ctx);
const started = vm.runInContext('startValidatedBackgroundSave', ctx)(snapshot);
setTimeout(() => console.log(JSON.stringify({ started, calls })), 20);
"""
    )

    assert out["started"] is True
    urls = [call["url"] for call in out["calls"]]
    assert urls == [
        "/api/books/book1/pages/page1/save",
        "/api/books/book1/pages/page1/save_segments",
    ]
    assert out["calls"][1]["body"]["segments"] == [
        {"segment_id": "SEG0001", "column_ids": ["COL0001", "COL0002"]}
    ]


def test_existing_initial_auto_save_candidate_is_unchanged() -> None:
    app_js = _read("static", "app.js")
    body = _function_body(app_js, "isInitialAutoSaveCandidate", "isAutoBoxExtendSaveCandidate")

    assert "!state.initialAutoGenerated || state.hasUserEdited" in body
    assert "!state.isDirty || state.isSegmentsDirty" in body
    assert "state.hasOriginalAnnotations" in body
    assert "state.columns.length === 0" in body
    assert "!hasUnassignedCharacters()" in body


def test_help_and_readme_document_auto_box_extend() -> None:
    template = _read("templates", "index.html")
    readme = _read("README.md")
    text = (
        "ボックスが1つも調整されていないページでは、読み込み時に現在の px 設定で自動的に全列を広げます。"
        "そのまま何も編集せずに次ページへ進むと、自動で保存されます。"
    )

    assert text in template
    assert text in readme


# ===== 列確定時の自動拡張（px 設定だけで効く） =====
def test_auto_extend_checkbox_is_removed_from_app_js_and_styles() -> None:
    app_js = _read("static", "app.js")
    css = _read("static", "style.css")

    for removed in (
        "columnBoxAutoExpand",
        "column-box-auto-expand",
        "isColumnBoxAutoExtendEnabled",
        "COLUMN_BOX_STORAGE_KEYS.autoExtend",
        "autoExtend:",
    ):
        assert removed not in app_js
    assert "column-box-panel__check" not in css
    assert "column-box-panel__check" not in _read("templates", "index.html")


def test_apply_auto_extend_is_gated_by_px_settings_only() -> None:
    body = _extract_function(_read("static", "app.js"), "applyAutoExtendToNewColumns")

    assert "px <= 0 && verticalPx <= 0" in body
    assert "getBulkExtendPx()" in body
    assert "getBulkExtendVerticalPx()" in body


def test_split_column_applies_auto_extend_on_every_branch() -> None:
    body = _extract_function(_read("static", "app.js"), "splitColumn")

    # 未割当2分岐 + 既存列内分割の2列 = 呼び出しは3か所
    assert body.count("applyAutoExtendToNewColumns(") == 3
    inner_start = body.index("// 既存の列内で分割")
    inner_end = body.index("// 未割り当ての文字の場合")
    inner = body[inner_start:inner_end]
    # null に戻した後で、分割した両列に適用する
    assert inner.index("state.columnBoxes[existingColIdx] = null") < inner.index("applyAutoExtendToNewColumns(")
    assert "applyAutoExtendToNewColumns([existingColIdx, existingColIdx + 1])" in inner


_SPLIT_HARNESS = r"""
const CHARS = [
  { x: 300, y: 50, width: 20, height: 20 },
  { x: 300, y: 80, width: 20, height: 20 },
  { x: 300, y: 110, width: 20, height: 20 },
  { x: 200, y: 50, width: 20, height: 20 },
  { x: 200, y: 80, width: 20, height: 20 },
];
const BASE_NAMES = [
  'splitColumn', 'getCharacterColumnIndex', 'sortColumns', 'applySandwichedUnassign',
  'removeCharacterFromColumns', 'applyAutoExtendToNewColumns', 'extendColumnBox',
  'getEffectiveColumnBox', 'getColumnUnionBox', 'getBboxForIndices', 'clampColumnBoxToImage',
  'isValidColumnBox', 'cloneColumnBox',
];

function runSplit(s) {
  const noop = () => {};
  const names = BASE_NAMES.slice();
  const context = {
    state: {
      characters: CHARS.map((c) => Object.assign({}, c)),
      columns: s.columns.map((c) => c.slice()),
      columnBoxes: s.columnBoxes.slice(),
      selectedIndex: s.selectedIndex,
      imageWidth: 400, imageHeight: 300, isDirty: false,
    },
    elements: {
      columnBoxExtendPx: { value: String(s.px) },
      columnBoxExtendVerticalPx: { value: String(s.verticalPx) },
    },
    COLUMN_BOX_MIN_SIZE: 8,
    COLUMN_BOX_DEFAULT_EXTEND_PX: 40,
    markUserEdited: noop, pruneInvalidWariGroups: noop, rebuildSegmentsFromCurrentColumns: noop,
    saveHistory: noop, draw: noop, updateStatus: noop, updateButtons: noop,
  };
  if (s.stubGetters) {
    // getBulkExtendPx は 0 以下を既定値 40 に直すため、実関数では 0 を渡せない。ゲートだけを検証する
    context.getBulkExtendPx = () => s.px;
    context.getBulkExtendVerticalPx = () => s.verticalPx;
  } else {
    names.push('getBulkExtendPx', 'getBulkExtendVerticalPx');
  }
  load(names, context);
  vm.runInContext('splitColumn()', context);
  return { columns: context.state.columns, columnBoxes: context.state.columnBoxes };
}
"""


def _run_split(
    columns: list[list[int]],
    column_boxes: list[dict[str, int] | None],
    selected_index: int,
    px: int = 40,
    vertical_px: int = 10,
    stub_getters: bool = False,
) -> dict[str, list]:
    scenario = json.dumps(
        {
            "columns": columns,
            "columnBoxes": column_boxes,
            "selectedIndex": selected_index,
            "px": px,
            "verticalPx": vertical_px,
            "stubGetters": stub_getters,
        }
    )
    result = _run_node(
        _SPLIT_HARNESS + f"console.log(JSON.stringify(runSplit({scenario})));"
    )
    assert isinstance(result, dict)
    return result


def test_split_column_extends_new_column_from_first_unassigned() -> None:
    # 文字 3,4 が未割当。先頭の未割当文字を選んで確定すると、列 [3, 4] に拡張が付く
    out = _run_split([[0, 1, 2]], [None], selected_index=3)

    assert out["columns"] == [[0, 1, 2], [3, 4]]
    # 文字範囲 x=200,y=50,w=20,h=50 → 右 +40、上下 +10 ずつ
    assert out["columnBoxes"] == [None, {"x": 200, "y": 40, "width": 60, "height": 70}]


def test_split_column_extends_new_column_before_selected_unassigned() -> None:
    # 未割当 0,1,2 の中ほど(1)を選ぶと、直前までの [0] が列になり、並べ替え後も拡張が付いたまま先頭に来る
    out = _run_split([[3, 4]], [None], selected_index=1)

    assert out["columns"] == [[0], [3, 4]]
    assert out["columnBoxes"] == [{"x": 300, "y": 40, "width": 60, "height": 40}, None]


def test_split_column_extends_both_columns_when_splitting_inside_existing_column() -> None:
    # 既存の調整済みボックスは捨てて、文字範囲から拡張し直す
    existing = {"x": 0, "y": 0, "width": 400, "height": 300}
    out = _run_split([[0, 1, 2]], [existing], selected_index=1)

    assert out["columns"] == [[0], [1, 2]]
    assert out["columnBoxes"] == [
        {"x": 300, "y": 40, "width": 60, "height": 40},
        {"x": 300, "y": 70, "width": 60, "height": 70},
    ]


def test_split_column_applies_right_px_only_when_vertical_px_is_zero() -> None:
    out = _run_split([[0, 1, 2]], [None], selected_index=3, px=40, vertical_px=0)

    assert out["columnBoxes"] == [None, {"x": 200, "y": 50, "width": 60, "height": 50}]


def test_split_column_does_not_extend_when_both_px_are_zero() -> None:
    new_column = _run_split(
        [[0, 1, 2]], [None], selected_index=3, px=0, vertical_px=0, stub_getters=True
    )
    inside = _run_split(
        [[0, 1, 2]], [None], selected_index=1, px=0, vertical_px=0, stub_getters=True
    )

    assert new_column["columns"] == [[0, 1, 2], [3, 4]]
    assert new_column["columnBoxes"] == [None, None]
    assert inside["columns"] == [[0], [1, 2]]
    assert inside["columnBoxes"] == [None, None]


def test_split_column_extends_when_only_vertical_px_is_positive() -> None:
    out = _run_split(
        [[0, 1, 2]], [None], selected_index=3, px=0, vertical_px=10, stub_getters=True
    )

    assert out["columnBoxes"] == [None, {"x": 200, "y": 40, "width": 20, "height": 70}]
