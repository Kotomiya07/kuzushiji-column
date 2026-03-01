/**
 * くずし字列分割アノテーションツール - Frontend Application
 */

// ===== 状態管理 =====
const state = {
    // データ
    bookId: null,
    pageId: null,
    pages: [],
    annotatedPages: [],
    lastAnnotated: null,  // 最後のアノテーション済みページ
    characters: [],
    imageWidth: 0,
    imageHeight: 0,
    
    // 選択状態
    selectedIndex: 0,
    
    viewMode: 'column',

    // セグメント編集（Column ID -> Segment ID）
    columnIds: [], // state.columns と同じ順序で Column ID を保持
    segments: [], // [[colIdx, colIdx, ...], ...] （セグメントごとの列インデックス）
    segmentIds: [], // state.segments と同じ順序で Segment ID を保持
    isSegmentsDirty: false,
    hasCustomSegmentIds: false,
    
    // 列分割状態（各列に含まれる文字のインデックス配列）
    columns: [],  // [[0, 1, 2], [3, 4, 5], ...]
    
    // Undo/Redo
    history: [],
    historyIndex: -1,
    
    // 変更フラグ
    isDirty: false,
    
    // 描画
    scale: 1,
    image: null,
    pixelRatio: 1,
    displayWidth: 0,
    displayHeight: 0,
    
    // ナビゲーション中フラグ（連続キー入力防止）
    isNavigating: false,
    
    // 元のアノテーション有無（DBに保存済みだったか）
    hasOriginalAnnotations: false,
};

// ===== 色の定義 (墨と和紙テーマ) =====
const COLORS = {
    default: 'rgba(90, 90, 90, 0.2)',
    defaultStroke: 'rgba(90, 90, 90, 0.6)',
    selected: 'rgba(199, 62, 58, 0.35)',
    selectedStroke: 'rgba(199, 62, 58, 1)',
    confirmed: [
        'rgba(74, 124, 89, 0.2)',
        'rgba(184, 134, 11, 0.2)',
        'rgba(70, 100, 140, 0.2)',
        'rgba(140, 90, 120, 0.2)',
        'rgba(160, 100, 80, 0.2)',
        'rgba(80, 120, 100, 0.2)',
        'rgba(150, 110, 70, 0.2)',
        'rgba(100, 130, 90, 0.2)',
    ],
    confirmedStroke: [
        'rgba(74, 124, 89, 0.8)',
        'rgba(184, 134, 11, 0.8)',
        'rgba(70, 100, 140, 0.8)',
        'rgba(140, 90, 120, 0.8)',
        'rgba(160, 100, 80, 0.8)',
        'rgba(80, 120, 100, 0.8)',
        'rgba(150, 110, 70, 0.8)',
        'rgba(100, 130, 90, 0.8)',
    ],
    centroidLine: 'rgba(199, 62, 58, 0.85)',
};

// ===== DOM要素 =====
const elements = {
    bookSelect: document.getElementById('book-select'),
    pageSelect: document.getElementById('page-select'),
    canvas: document.getElementById('annotation-canvas'),
    loading: document.getElementById('loading'),
    btnPrev: document.getElementById('btn-prev'),
    btnNext: document.getElementById('btn-next'),
    btnUndo: document.getElementById('btn-undo'),
    btnRedo: document.getElementById('btn-redo'),
    btnConfirmColumn: document.getElementById('btn-confirm-column'),
    btnUnassign: document.getElementById('btn-unassign'),
    btnNavUp: document.getElementById('btn-nav-up'),
    btnNavDown: document.getElementById('btn-nav-down'),
    btnSave: document.getElementById('btn-save'),
    btnClear: document.getElementById('btn-clear'),
    statusColumnsLabel: document.getElementById('status-columns-label'),
    statusColumns: document.getElementById('status-columns'),
    statusCurrent: document.getElementById('status-current'),
    statusRemaining: document.getElementById('status-remaining'),
    viewToggleSeg: document.getElementById('view-toggle-seg'),
    saveDialog: document.getElementById('save-dialog'),
    dialogCancel: document.getElementById('dialog-cancel'),
    dialogDiscard: document.getElementById('dialog-discard'),
    dialogSave: document.getElementById('dialog-save'),
    toast: document.getElementById('toast'),
    autoToggle: document.getElementById('auto-toggle'),
    btnHelp: document.getElementById('btn-help'),
    helpDialog: document.getElementById('help-dialog'),
    helpClose: document.getElementById('help-close'),
};

const ctx = elements.canvas.getContext('2d');

// ===== トースト通知 =====
let toastTimeout = null;

function unicodeToChar(unicode) {
    if (!unicode) return '';
    const s = String(unicode).trim();
    const m = s.match(/^U\+([0-9A-Fa-f]{4,6})$/);
    if (!m) return '';
    const cp = Number.parseInt(m[1], 16);
    if (!Number.isFinite(cp)) return '';
    try {
        return String.fromCodePoint(cp);
    } catch {
        return '';
    }
}

function showToast(message, isError = false) {
    // 既存のタイマーをクリア
    if (toastTimeout) {
        clearTimeout(toastTimeout);
    }
    
    elements.toast.textContent = message;
    elements.toast.classList.remove('hidden', 'error');
    if (isError) {
        elements.toast.classList.add('error');
    }
    
    // 3秒後に非表示
    toastTimeout = setTimeout(() => {
        elements.toast.classList.add('hidden');
    }, 3000);
}

// ===== 初期化 =====
async function init() {
    await loadBooks();
    setupEventListeners();

        if (elements.viewToggleSeg) {
            state.viewMode = elements.viewToggleSeg.checked ? 'segment' : 'column';
            updateStatus();
        }

}

// ===== API呼び出し =====
async function loadBooks() {
    try {
        const res = await fetch('/api/books');
        const data = await res.json();
        
        elements.bookSelect.innerHTML = '<option value="">書籍を選択...</option>';
        data.books.forEach(book => {
            const option = document.createElement('option');
            option.value = book.book_id;
            option.textContent = `${book.book_id} (${book.progress}%)`;
            elements.bookSelect.appendChild(option);
        });
        
        // 起動時に最初の書籍を自動で開く
        if (data.books.length > 0) {
            const firstBook = data.books[0];
            elements.bookSelect.value = firstBook.book_id;
            await loadPages(firstBook.book_id);
            
            // アノテーション済みページがあればその最後を、なければ最初のページを開く
            if (state.pages.length > 0) {
                const targetPageId = state.lastAnnotated || state.pages[0];
                elements.pageSelect.value = targetPageId;
                await loadPageData(firstBook.book_id, targetPageId);
            }
        }
    } catch (error) {
        console.error('書籍一覧の取得に失敗:', error);
    }
}

async function updateBookProgress() {
    // 現在選択中の書籍の進捗率を更新
    try {
        const res = await fetch('/api/books');
        const data = await res.json();
        
        // 現在選択中の書籍のoptionを更新
        const currentBookId = state.bookId;
        const book = data.books.find(b => b.book_id === currentBookId);
        
        if (book) {
            const options = elements.bookSelect.options;
            for (let i = 0; i < options.length; i++) {
                if (options[i].value === currentBookId) {
                    options[i].textContent = `${book.book_id} (${book.progress}%)`;
                    break;
                }
            }
        }
    } catch (error) {
        console.error('進捗率の更新に失敗:', error);
    }
}

async function loadPages(bookId) {
    try {
        const res = await fetch(`/api/books/${bookId}/pages`);
        const data = await res.json();
        
        state.pages = data.pages;
        state.annotatedPages = data.annotated_pages || [];
        state.lastAnnotated = data.last_annotated;  // 最後のアノテーション済みページ

        const annotatedSet = new Set(state.annotatedPages);
        
        elements.pageSelect.innerHTML = '<option value="">ページを選択...</option>';
        data.pages.forEach(page => {
            const option = document.createElement('option');
            option.value = page;
            option.textContent = annotatedSet.has(page) ? `${page} ✅️` : page;
            elements.pageSelect.appendChild(option);
        });
    } catch (error) {
        console.error('ページ一覧の取得に失敗:', error);
    }
}

async function loadPageData(bookId, pageId) {
    showLoading(true);
    
    try {
        // ページデータと画像を並列で読み込み
        const [dataRes, imageUrl] = await Promise.all([
            fetch(`/api/books/${bookId}/pages/${pageId}`),
            loadImage(`/api/books/${bookId}/pages/${pageId}/image`),
        ]);
        
        const data = await dataRes.json();
        
        state.bookId = bookId;
        state.pageId = pageId;
        state.characters = data.characters;
        state.imageWidth = data.image_width;
        state.imageHeight = data.image_height;
        state.image = imageUrl;
        
        // 既存のアノテーションから列を復元
        restoreColumnsFromAnnotations();

        // 既存のセグメント（output_seg）から復元（なければ自動推定）
        restoreSegmentsFromAnnotations();
        
        // DBに元のアノテーションがあったかを記録
        const hasExistingAnnotations = state.columns.length > 0;
        state.hasOriginalAnnotations = hasExistingAnnotations;
        if (!hasExistingAnnotations && elements.autoToggle.checked) {
            autoEstimateColumns();
        }

        // 未分割に挟まれた文字を未分割に戻す
        const sandwichedRemoved = applySandwichedUnassign();
        if (sandwichedRemoved) {
            sortColumns();
        }
        
        // 選択をリセット（最初の未分割文字、またはなければ最初の文字）
        state.selectedIndex = findFirstUnassignedIndex();
        
        // 履歴をリセットし、初期状態を保存
        state.history = [{
            columns: JSON.parse(JSON.stringify(state.columns)),
            columnIds: JSON.parse(JSON.stringify(state.columnIds)),
            segments: JSON.parse(JSON.stringify(state.segments)),
            segmentIds: JSON.parse(JSON.stringify(state.segmentIds)),
            selectedIndex: state.selectedIndex,
            isSegmentsDirty: state.isSegmentsDirty,
        }];
        state.historyIndex = 0;
        
        // Auto分割した場合はdirtyとしてマーク（保存確認を出すため）
        state.isDirty = (!hasExistingAnnotations && elements.autoToggle.checked && state.columns.length > 0) || sandwichedRemoved;
        
        // 描画
         calculateScale();
                draw();
                updateStatus();
                updateButtons();
         updateButtons();
        
    } catch (error) {
        console.error('ページデータの取得に失敗:', error);
    } finally {
        showLoading(false);
    }
}

function loadImage(url) {
    return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = url;
    });
}

async function saveAnnotations() {
    if (state.columns.length === 0) {
        showToast('保存する列がありません', true);
        return;
    }

    const assigned = new Set(state.columns.flat());
    const remaining = state.characters.length - assigned.size;
    if (remaining > 0) {
        window.alert(`未分割の文字が${remaining}件あります。すべて分割してから保存してください。`);
        return;
    }
    
    showLoading(true);
    
    try {
        const columnsData = state.columns.map(indices => ({
            char_ids: indices.map(i => state.characters[i].char_id),
        }));
        
        const res = await fetch(`/api/books/${state.bookId}/pages/${state.pageId}/save`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                book_id: state.bookId,
                page_id: state.pageId,
                columns: columnsData,
            }),
        });
        
        const result = await res.json();
        
        if (result.success) {
            state.isDirty = false;
            state.hasOriginalAnnotations = true;

            // このセッション内で Column ID を同期（サーバ側も同じ連番を割当てる）
            state.columnIds = state.columns.map((_, i) => `COL${String(i + 1).padStart(4, '0')}`);
            state.columns.forEach((indices, colIdx) => {
                const colId = state.columnIds[colIdx];
                indices.forEach((charIdx) => {
                    if (state.characters[charIdx]) state.characters[charIdx].column_id = colId;
                });
            });
            restoreColumnsFromAnnotations();
            restoreSegmentsFromAnnotations();

            showToast(result.message);
            updateButtons();
            await updateBookProgress();
        } else {
            showToast('保存に失敗しました', true);
        }
    } catch (error) {
        console.error('保存に失敗:', error);
        showToast('保存に失敗しました', true);
    } finally {
        showLoading(false);
    }
}

// ===== 列管理 =====
function restoreColumnsFromAnnotations() {
    state.columns = [];
    state.columnIds = [];
    
    // Column IDでグループ化
    const columnMap = new Map();
    state.characters.forEach((char, index) => {
        if (char.column_id) {
            if (!columnMap.has(char.column_id)) {
                columnMap.set(char.column_id, []);
            }
            columnMap.get(char.column_id).push(index);
        }
    });
    
    // Column IDでソートして配列に変換
    const sortedIds = Array.from(columnMap.keys()).sort();
    sortedIds.forEach(colId => {
        state.columnIds.push(colId);
        state.columns.push(columnMap.get(colId));
    });
}

function restoreSegmentsFromAnnotations() {
    // 既存の Segment ID があればそれを復元、なければ自動推定結果を初期値にする
    const perColumnSegId = state.columns.map((col) => {
        if (!col || col.length === 0) return '';
        const firstChar = state.characters[col[0]];
        return firstChar && firstChar.segment_id ? String(firstChar.segment_id) : '';
    });

    const hasAnySeg = perColumnSegId.some((s) => s && s.trim().length > 0);

    const segments = [];
    const segmentIds = [];

    if (state.columns.length === 0) {
        state.segments = [];
        state.segmentIds = [];
        state.isSegmentsDirty = false;
        return;
    }

    if (hasAnySeg) {
        // Column ID順の連続区間としてセグメントを復元
        let currentCols = [0];
        let currentSegId = (perColumnSegId[0] || '').trim();

        for (let i = 1; i < state.columns.length; i++) {
            const segId = (perColumnSegId[i] || '').trim();
            if (segId && currentSegId && segId === currentSegId) {
                currentCols.push(i);
                continue;
            }

            segments.push(currentCols);
            segmentIds.push(currentSegId);
            currentCols = [i];
            currentSegId = segId;
        }
        segments.push(currentCols);
        segmentIds.push(currentSegId);

        // 空の Segment ID が混ざる場合は連番で補完
        const used = new Set(segmentIds.filter((s) => s));
        let nextNum = 1;
        const nextId = () => {
            while (used.has(`SEG${String(nextNum).padStart(4, '0')}`)) nextNum++;
            const id = `SEG${String(nextNum).padStart(4, '0')}`;
            used.add(id);
            nextNum++;
            return id;
        };

        for (let i = 0; i < segmentIds.length; i++) {
            if (!segmentIds[i]) segmentIds[i] = nextId();
        }
    } else {
        // 自動推定（列配列からセグメントを構築）
        const autoSegCols = buildSegmentsFromColumns();
        autoSegCols.forEach((segCols, idx) => {
            const colIdxes = segCols.map((col) => state.columns.indexOf(col)).filter((i) => i >= 0);
            segments.push(colIdxes);
            segmentIds.push(`SEG${String(idx + 1).padStart(4, '0')}`);
        });
    }

    state.segments = segments;
    state.segmentIds = segmentIds;
    normalizeSegmentsById();
    state.isSegmentsDirty = false;
}

function normalizeSegmentsById() {
    // 同一 Segment ID が複数セグメントに分かれている場合は統合する
    // （Segment ID = セグメントの同一性、という期待に合わせる）
    if (!state.segments || state.segments.length === 0) return;

    const byId = new Map();
    for (let i = 0; i < state.segments.length; i++) {
        const segId = (state.segmentIds[i] || '').trim();
        const cols = state.segments[i] || [];
        if (!segId) continue;
        if (!byId.has(segId)) byId.set(segId, []);
        byId.get(segId).push(...cols);
    }

    if (byId.size === 0) return;

    const items = [];
    byId.forEach((cols, segId) => {
        const unique = Array.from(new Set(cols)).sort((a, b) => a - b);
        if (unique.length === 0) return;
        items.push({ segId, cols: unique, min: unique[0] });
    });

    items.sort((a, b) => a.min - b.min);
    state.segments = items.map((it) => it.cols);
    state.segmentIds = items.map((it) => it.segId);

    // すべての列がちょうど一度だけ現れるように補正（念のため）
    const totalCols = state.columns.length;
    const assigned = new Set(state.segments.flat());
    const missing = [];
    for (let i = 0; i < totalCols; i++) {
        if (!assigned.has(i)) missing.push(i);
    }
    if (missing.length > 0) {
        const used = new Set(state.segmentIds);
        let nextNum = 1;
        while (used.has(`SEG${String(nextNum).padStart(4, '0')}`)) nextNum++;
        const newSegId = `SEG${String(nextNum).padStart(4, '0')}`;
        state.segments.push(missing);
        state.segmentIds.push(newSegId);
    }
}

function renumberSegmentIdsIfAuto() {
    // Segment ID の連番を維持したいケース向け。
    // 明示的にID変更（rename）した場合は自動連番を止める。
    if (state.hasCustomSegmentIds) return;
    if (!state.segmentIds || state.segmentIds.length === 0) return;

    state.segmentIds = state.segmentIds.map((_, idx) => `SEG${String(idx + 1).padStart(4, '0')}`);
}

function getSelectedColumnIndex() {
    return getCharacterColumnIndex(state.selectedIndex);
}

function getSegmentIndexForColumn(colIdx) {
    for (let segIdx = 0; segIdx < state.segments.length; segIdx++) {
        const cols = state.segments[segIdx];
        const pos = cols.indexOf(colIdx);
        if (pos !== -1) return { segIdx, pos };
    }
    return null;
}

function canSplitSegmentAtSelection() {
    const colIdx = getSelectedColumnIndex();
    if (colIdx < 0) return false;
    const hit = getSegmentIndexForColumn(colIdx);
    if (!hit) return false;
    const cols = state.segments[hit.segIdx];
    return cols.length >= 2 && hit.pos >= 1;
}

function canJoinSegmentAtSelection() {
    const colIdx = getSelectedColumnIndex();
    if (colIdx < 0) return false;
    const hit = getSegmentIndexForColumn(colIdx);
    if (!hit) return false;
    // 選択中の列が属するセグメントを「前のセグメント」と結合する
    return hit.segIdx >= 1;
}

function splitSegment() {
    if (state.viewMode !== 'segment') return;
    if (!canSplitSegmentAtSelection()) return;

    const colIdx = getSelectedColumnIndex();
    const hit = getSegmentIndexForColumn(colIdx);
    if (!hit) return;

    const cols = state.segments[hit.segIdx];
    const left = cols.slice(0, hit.pos);
    const right = cols.slice(hit.pos);

    const used = new Set(state.segmentIds);
    let nextNum = 1;
    while (used.has(`SEG${String(nextNum).padStart(4, '0')}`)) nextNum++;
    const newSegId = `SEG${String(nextNum).padStart(4, '0')}`;

    saveHistory();
    state.segments.splice(hit.segIdx, 1, left, right);
    state.segmentIds.splice(hit.segIdx, 1, state.segmentIds[hit.segIdx], newSegId);
    normalizeSegmentsById();
    renumberSegmentIdsIfAuto();
    state.isSegmentsDirty = true;
    draw();
    updateStatus();
    updateButtons();
}

function joinSegment() {
    if (state.viewMode !== 'segment') return;
    if (!canJoinSegmentAtSelection()) return;

    const colIdx = getSelectedColumnIndex();
    const hit = getSegmentIndexForColumn(colIdx);
    if (!hit) return;

    saveHistory();
    const prevCols = state.segments[hit.segIdx - 1];
    const curCols = state.segments[hit.segIdx];
    state.segments[hit.segIdx - 1] = prevCols.concat(curCols);
    state.segments.splice(hit.segIdx, 1);
    state.segmentIds.splice(hit.segIdx, 1);
    normalizeSegmentsById();
    renumberSegmentIdsIfAuto();
    state.isSegmentsDirty = true;
    draw();
    updateStatus();
    updateButtons();
}

function renameSegment() {
    if (state.viewMode !== 'segment') return;
    const colIdx = getSelectedColumnIndex();
    if (colIdx < 0) return;
    const hit = getSegmentIndexForColumn(colIdx);
    if (!hit) return;

    const current = state.segmentIds[hit.segIdx] || '';
    const next = window.prompt('Segment ID を入力（例: SEG0001）', current);
    if (next === null) return;
    const trimmed = next.trim();
    if (!/^SEG\d{4}$/.test(trimmed)) {
        window.alert('Segment ID は SEG0001 形式のみ対応です');
        return;
    }

    if (trimmed === current) return;

    saveHistory();
    state.segmentIds[hit.segIdx] = trimmed;
    state.hasCustomSegmentIds = true;
    normalizeSegmentsById();
    state.isSegmentsDirty = true;
    draw();
    updateStatus();
    updateButtons();
}

function hasUnassignedCharacters() {
    // 未分割の文字があるかどうかを判定
    const assigned = new Set(state.columns.flat());
    return state.characters.some((_, i) => !assigned.has(i));
}

function autoEstimateColumns() {
    // X座標（bboxの中心）でグループ化して列を推定
    if (state.characters.length === 0) return;
    
    // 各文字のX中心座標を計算
    const charsWithCenter = state.characters.map((char, index) => ({
        index,
        centerX: char.x + char.width / 2,
        centerY: char.y + char.height / 2,
    }));
    
    // X座標でソート（右から左 = 降順）
    const sortedByX = [...charsWithCenter].sort((a, b) => b.centerX - a.centerX);
    
    // 平均文字幅を計算してしきい値を決定
    const avgWidth = state.characters.reduce((sum, c) => sum + c.width, 0) / state.characters.length;
    const threshold = avgWidth * 0.8; // 文字幅の80%以上離れていたら別の列
    
    // 列をグループ化
    const columns = [];
    let currentColumn = [sortedByX[0]];
    
    for (let i = 1; i < sortedByX.length; i++) {
        const prev = sortedByX[i - 1];
        const curr = sortedByX[i];
        
        // X座標の差がしきい値を超えたら新しい列
        if (prev.centerX - curr.centerX > threshold) {
            columns.push(currentColumn);
            currentColumn = [curr];
        } else {
            currentColumn.push(curr);
        }
    }
    columns.push(currentColumn);
    
    // 各列内でY座標でソート（上から下）して、インデックスの配列に変換
    const rawColumns = columns.map(col => {
        col.sort((a, b) => a.centerY - b.centerY);
        return col.map(c => c.index);
    });
    
    // Char IDが連続するように後処理
    // 各文字がどの列に属するかのマッピングを作成
    const charToColumn = new Array(state.characters.length).fill(-1);
    rawColumns.forEach((col, colIdx) => {
        col.forEach(charIdx => {
            charToColumn[charIdx] = colIdx;
        });
    });
    
    // Char ID順（インデックス順）に走査して、連続性を確保
    // 前後の文字と同じ列に入れる
    for (let i = 1; i < state.characters.length - 1; i++) {
        const prevCol = charToColumn[i - 1];
        const currCol = charToColumn[i];
        const nextCol = charToColumn[i + 1];
        
        // 前後が同じ列で、自分だけ違う列の場合、前後に合わせる
        if (prevCol === nextCol && currCol !== prevCol) {
            charToColumn[i] = prevCol;
        }
    }
    
    // 列ごとにグループ化し直す（Char ID順を維持）
    const finalColumns = [];
    let currentCol = -1;
    let currentGroup = [];
    
    for (let i = 0; i < state.characters.length; i++) {
        const col = charToColumn[i];
        if (col !== currentCol) {
            if (currentGroup.length > 0) {
                finalColumns.push(currentGroup);
            }
            currentCol = col;
            currentGroup = [i];
        } else {
            currentGroup.push(i);
        }
    }
    if (currentGroup.length > 0) {
        finalColumns.push(currentGroup);
    }
    
    state.columns = finalColumns;
}

function findFirstUnassignedIndex() {
    const assigned = new Set(state.columns.flat());
    for (let i = 0; i < state.characters.length; i++) {
        if (!assigned.has(i)) {
            return i;
        }
    }
    return 0;
}

function getCharacterColumnIndex(charIndex) {
    for (let colIdx = 0; colIdx < state.columns.length; colIdx++) {
        if (state.columns[colIdx].includes(charIndex)) {
            return colIdx;
        }
    }
    return -1;
}

function sortColumns() {
    // 各列の最小Char ID（インデックス）でソートして列番号をChar ID順にする
    state.columns.sort((a, b) => {
        const minA = Math.min(...a);
        const minB = Math.min(...b);
        return minA - minB;
    });
}

function removeCharacterFromColumns(charIndex) {
    const colIdx = getCharacterColumnIndex(charIndex);
    if (colIdx === -1) return false;
    
    const col = state.columns[colIdx];
    const posInCol = col.indexOf(charIndex);
    if (posInCol === -1) return false;
    
    col.splice(posInCol, 1);
    if (col.length === 0) {
        state.columns.splice(colIdx, 1);
    }
    return true;
}

function applySandwichedUnassign() {
    if (state.characters.length < 3) return false;
    
    const assigned = new Set(state.columns.flat());
    const assignedFlags = state.characters.map((_, idx) => assigned.has(idx));
    const targets = [];
    
    let i = 0;
    while (i < assignedFlags.length) {
        const isAssigned = assignedFlags[i];
        const start = i;
        
        while (i < assignedFlags.length && assignedFlags[i] === isAssigned) {
            i++;
        }
        
        const end = i - 1;
        if (!isAssigned) {
            continue;
        }
        
        const hasUnassignedBefore = start > 0 && !assignedFlags[start - 1];
        const hasUnassignedAfter = end < assignedFlags.length - 1 && !assignedFlags[end + 1];
        
        if (hasUnassignedBefore && hasUnassignedAfter) {
            for (let j = start; j <= end; j++) {
                targets.push(j);
            }
        }
    }
    
    if (targets.length === 0) return false;
    
    targets.forEach((idx) => removeCharacterFromColumns(idx));
    return true;
}

function unassignCharacter() {
    const selectedIdx = state.selectedIndex;
    const removed = removeCharacterFromColumns(selectedIdx);
    if (!removed) return; // Already unassigned
    
    applySandwichedUnassign();
    
    state.isDirty = true;
    sortColumns();
    saveHistory();
    draw();
    updateStatus();
    updateButtons();
}

function clearAllAssignments() {
    if (state.columns.length === 0) return;
    
    state.columns = [];
    state.isDirty = true;
    saveHistory();
    draw();
    updateStatus();
    updateButtons();
}

function splitColumn() {
    // 選択した文字から新しい列が始まる
    // つまり、選択した文字の「直前」までが前の列として確定される
    const selectedIdx = state.selectedIndex;
    
    // 未割り当ての文字を取得
    const assigned = new Set(state.columns.flat());
    const unassigned = [];
    for (let i = 0; i < state.characters.length; i++) {
        if (!assigned.has(i)) {
            unassigned.push(i);
        }
    }
    
    // 未割り当ての文字がなければ何もしない
    if (unassigned.length === 0) {
        return;
    }
    
    // 既存の列に含まれているか確認
    const existingColIdx = getCharacterColumnIndex(selectedIdx);
    
    if (existingColIdx !== -1) {
        // 既存の列内で分割（選択した文字から新しい列が始まる）
        const col = state.columns[existingColIdx];
        const posInCol = col.indexOf(selectedIdx);
        
        if (posInCol > 0) {
            // 分割する：選択した文字から新しい列
            const newCol = col.splice(posInCol);
            state.columns.splice(existingColIdx + 1, 0, newCol);
            state.isDirty = true;
            sortColumns();
            saveHistory();
        }
    } else {
        // 未割り当ての文字の場合
        const firstUnassigned = unassigned[0];
        const lastUnassigned = unassigned[unassigned.length - 1];
        
        // 最初の未割り当て文字を選択している場合
        // または最後の未割り当て文字を選択している場合は、残り全部を列として確定
        if (selectedIdx === firstUnassigned || selectedIdx === lastUnassigned) {
            const newCol = [...unassigned];
            if (newCol.length > 0) {
                state.columns.push(newCol);
                state.isDirty = true;
                sortColumns();
                saveHistory();
            }
        } else {
            // 選択した文字の「直前」までの未割り当て文字を列として確定
            const newCol = [];
            
            // 選択した文字の直前までを列に追加（選択した文字は含まない）
            for (let i = 0; i < state.characters.length && i < selectedIdx; i++) {
                if (!assigned.has(i)) {
                    newCol.push(i);
                }
            }
            
            if (newCol.length > 0) {
                state.columns.push(newCol);
                state.isDirty = true;
                sortColumns();
                saveHistory();
            }
        }
    }

    const sandwichedRemoved = applySandwichedUnassign();
    if (sandwichedRemoved) {
        state.isDirty = true;
        sortColumns();
        saveHistory();
    }
    
    draw();
    updateStatus();
    updateButtons();
}

function moveToNextUnassigned() {
    const assigned = new Set(state.columns.flat());
    for (let i = state.selectedIndex + 1; i < state.characters.length; i++) {
        if (!assigned.has(i)) {
            state.selectedIndex = i;
            return;
        }
    }
    // 見つからなければ最後の文字
    if (state.characters.length > 0) {
        state.selectedIndex = state.characters.length - 1;
    }
}

// ===== Undo/Redo =====
function saveHistory() {
    // 現在位置より後の履歴を削除
    state.history = state.history.slice(0, state.historyIndex + 1);
    
    // 新しい状態を保存（操作後に呼ばれるので、操作後の状態を保存）
    state.history.push({
        columns: JSON.parse(JSON.stringify(state.columns)),
        columnIds: JSON.parse(JSON.stringify(state.columnIds)),
        segments: JSON.parse(JSON.stringify(state.segments)),
        segmentIds: JSON.parse(JSON.stringify(state.segmentIds)),
        selectedIndex: state.selectedIndex,
        isSegmentsDirty: state.isSegmentsDirty,
    });
    state.historyIndex = state.history.length - 1;
}

function undo() {
    // historyIndex > 0 なら前の状態に戻れる
    if (state.historyIndex > 0) {
        state.historyIndex--;
        const snapshot = state.history[state.historyIndex];
        state.columns = JSON.parse(JSON.stringify(snapshot.columns));
        state.columnIds = JSON.parse(JSON.stringify(snapshot.columnIds || []));
        state.segments = JSON.parse(JSON.stringify(snapshot.segments || []));
        state.segmentIds = JSON.parse(JSON.stringify(snapshot.segmentIds || []));
        state.selectedIndex = snapshot.selectedIndex;
        state.isDirty = state.historyIndex > 0; // 初期状態に戻ったらdirtyではない
        state.isSegmentsDirty = Boolean(snapshot.isSegmentsDirty);
        draw();
        updateStatus();
        updateButtons();
    }
}

function redo() {
    if (state.historyIndex < state.history.length - 1) {
        state.historyIndex++;
        const snapshot = state.history[state.historyIndex];
        state.columns = JSON.parse(JSON.stringify(snapshot.columns));
        state.columnIds = JSON.parse(JSON.stringify(snapshot.columnIds || []));
        state.segments = JSON.parse(JSON.stringify(snapshot.segments || []));
        state.segmentIds = JSON.parse(JSON.stringify(snapshot.segmentIds || []));
        state.selectedIndex = snapshot.selectedIndex;
        state.isDirty = true;
        state.isSegmentsDirty = Boolean(snapshot.isSegmentsDirty);
        draw();
        updateStatus();
        updateButtons();
    }
}

// ===== 描画 =====
function calculateScale() {
    const container = elements.canvas.parentElement;
    const maxWidth = container.clientWidth;
    const maxHeight = container.clientHeight;
    
    const scaleX = maxWidth / state.imageWidth;
    const scaleY = maxHeight / state.imageHeight;
    
    // 画面に収まるようにスケールを調整（上限なし）
    state.scale = Math.min(scaleX, scaleY);
    
    state.displayWidth = state.imageWidth * state.scale;
    state.displayHeight = state.imageHeight * state.scale;

    state.pixelRatio = window.devicePixelRatio || 1;
    elements.canvas.style.width = `${state.displayWidth}px`;
    elements.canvas.style.height = `${state.displayHeight}px`;
    elements.canvas.width = Math.round(state.displayWidth * state.pixelRatio);
    elements.canvas.height = Math.round(state.displayHeight * state.pixelRatio);
    ctx.setTransform(state.pixelRatio, 0, 0, state.pixelRatio, 0, 0);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
}

function draw() {
    if (!state.image) return;
    
    const { scale } = state;
    
    // 画像を描画
    ctx.drawImage(state.image, 0, 0, state.displayWidth, state.displayHeight);
    
     if (state.viewMode === 'segment') {
         const segments = (state.segments && state.segments.length > 0) ? state.segments : [];
         segments.forEach((segColIdxes, segIdx) => {
             const segCols = segColIdxes.map((i) => state.columns[i]).filter(Boolean);
             const colorIdx = segIdx % COLORS.confirmed.length;
             drawSegmentBbox(segCols, COLORS.confirmed[colorIdx], COLORS.confirmedStroke[colorIdx]);
         });
     } else {
         state.columns.forEach((col, colIdx) => {
             const colorIdx = colIdx % COLORS.confirmed.length;
             drawColumnBbox(col, COLORS.confirmed[colorIdx], COLORS.confirmedStroke[colorIdx]);
         });
     }

    
     const assigned = new Set(state.columns.flat());
     state.characters.forEach((_, idx) => {
         if (idx !== state.selectedIndex) {
             const colIdx = getCharacterColumnIndex(idx);
             if (colIdx >= 0) {
                 const colorIdx = colIdx % COLORS.confirmedStroke.length;
                 drawBboxStrokeOnly(idx, COLORS.confirmedStroke[colorIdx], 1);
             } else {
                 drawBboxStrokeOnly(idx, COLORS.defaultStroke, 1);
             }
         }
     });

    
    // 選択中の文字を描画（最後に描画して最前面に、ハイライト付き）
    drawBbox(state.selectedIndex, COLORS.selected, COLORS.selectedStroke, 3);
    
    // 重心を結ぶ線を描画
    drawCentroidLine();
}

function getBboxForIndices(indices) {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;

    indices.forEach(charIdx => {
        const char = state.characters[charIdx];
        if (!char) return;
        minX = Math.min(minX, char.x);
        minY = Math.min(minY, char.y);
        maxX = Math.max(maxX, char.x + char.width);
        maxY = Math.max(maxY, char.y + char.height);
    });

    if (!Number.isFinite(minX) || !Number.isFinite(minY) || !Number.isFinite(maxX) || !Number.isFinite(maxY)) {
        return null;
    }

    return { minX, minY, maxX, maxY };
}

function buildSegmentsFromColumns() {
    if (state.columns.length === 0) return [];

    const colBoxes = state.columns.map((col) => {
        const bbox = getBboxForIndices(col);
        if (!bbox) return null;
        const left = bbox.minX;
        const right = bbox.maxX;
        const top = bbox.minY;
        const bottom = bbox.maxY;
        const cw = Math.max(1, right - left);
        const ch = Math.max(1, bottom - top);
        return { left, right, top, bottom, cw, ch, indices: col };
    }).filter(Boolean);

    if (colBoxes.length === 0) return [];

    const xTh = 0.6;
    const stackedXTh = 0.35;
    const stackedYTh = 0.1;
    const rectOverlapThClosed = 0.1;
    const rectOverlapThFuture = 0.05;

    function rectForCols(cols) {
        let l = Infinity;
        let t = Infinity;
        let r = -Infinity;
        let b = -Infinity;

        cols.forEach((col) => {
            const bbox = getBboxForIndices(col);
            if (!bbox) return;
            l = Math.min(l, bbox.minX);
            t = Math.min(t, bbox.minY);
            r = Math.max(r, bbox.maxX);
            b = Math.max(b, bbox.maxY);
        });

        if (!Number.isFinite(l) || !Number.isFinite(t) || !Number.isFinite(r) || !Number.isFinite(b)) {
            return null;
        }

        return { l, t, r, b };
    }

    function overlapRatio(a, b) {
        const interW = Math.max(0, Math.min(a.r, b.r) - Math.max(a.l, b.l));
        const interH = Math.max(0, Math.min(a.b, b.b) - Math.max(a.t, b.t));
        const inter = interW * interH;
        if (inter <= 0) return 0;

        const areaA = Math.max(1, (a.r - a.l) * (a.b - a.t));
        const areaB = Math.max(1, (b.r - b.l) * (b.b - b.t));
        return inter / Math.min(areaA, areaB);
    }

    function xOverlapRatio(a, b) {
        const xInter = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
        const minW = Math.max(1, Math.min(a.cw, b.cw));
        return xInter / minW;
    }

    function yOverlapRatio(a, b) {
        const yInter = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
        const minH = Math.max(1, Math.min(a.ch, b.ch));
        return yInter / minH;
    }

    const segments = [];
    const closedRects = [];

    let current = [colBoxes[0].indices];
    let currentRect = rectForCols(current);

    function closeCurrent() {
        if (current.length === 0) return;
        segments.push(current);
        if (currentRect) {
            closedRects.push(currentRect);
        }
    }

    for (let i = 1; i < colBoxes.length; i++) {
        const curBox = colBoxes[i];
        const curCol = curBox.indices;

        const candCols = current.concat([curCol]);
        const candRect = rectForCols(candCols);
        if (!candRect) {
            closeCurrent();
            current = [curCol];
            currentRect = rectForCols(current);
            continue;
        }

        if (closedRects.some(r => overlapRatio(candRect, r) > rectOverlapThClosed)) {
            closeCurrent();
            current = [curCol];
            currentRect = rectForCols(current);
            continue;
        }

        const currentBoxes = current.map((col) => {
            const bbox = getBboxForIndices(col);
            if (!bbox) return null;
            const left = bbox.minX;
            const right = bbox.maxX;
            const top = bbox.minY;
            const bottom = bbox.maxY;
            const cw = Math.max(1, right - left);
            const ch = Math.max(1, bottom - top);
            return { left, right, top, bottom, cw, ch };
        }).filter(Boolean);

        if (currentBoxes.some((b) => {
            const xOver = xOverlapRatio(b, curBox);
            const yOver = yOverlapRatio(b, curBox);
            return (xOver >= xTh) || (xOver >= stackedXTh && yOver <= stackedYTh);
        })) {
            closeCurrent();
            current = [curCol];
            currentRect = rectForCols(current);
            continue;
        }

        const candBoxes = currentBoxes.concat([curBox]);

        let intersectsFuture = false;
        for (let j = i + 1; j < colBoxes.length; j++) {
            const fut = colBoxes[j];
            if (!candBoxes.some((b) => xOverlapRatio(b, fut) >= xTh)) continue;
            const futRect = { l: fut.left, t: fut.top, r: fut.right, b: fut.bottom };
            if (overlapRatio(candRect, futRect) > rectOverlapThFuture) {
                intersectsFuture = true;
                break;
            }
        }

        if (intersectsFuture) {
            closeCurrent();
            current = [curCol];
            currentRect = rectForCols(current);
            continue;
        }

        current = candCols;
        currentRect = candRect;
    }

    closeCurrent();
    return segments;
}

function drawSegmentBbox(segmentCols, fillColor, strokeColor) {
    const merged = segmentCols.flat();
    const bbox = getBboxForIndices(merged);
    if (!bbox) return;

    const { scale } = state;
    const x = bbox.minX * scale;
    const y = bbox.minY * scale;
    const w = (bbox.maxX - bbox.minX) * scale;
    const h = (bbox.maxY - bbox.minY) * scale;

    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
}

function drawColumnBbox(columnIndices, fillColor, strokeColor) {
    if (columnIndices.length === 0) return;

    const bbox = getBboxForIndices(columnIndices);
    if (!bbox) return;

    const { scale } = state;
    const x = bbox.minX * scale;
    const y = bbox.minY * scale;
    const w = (bbox.maxX - bbox.minX) * scale;
    const h = (bbox.maxY - bbox.minY) * scale;

    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);

    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 2;
    ctx.strokeRect(x, y, w, h);
}

function drawBboxStrokeOnly(charIndex, strokeColor, lineWidth) {
    const char = state.characters[charIndex];
    if (!char) return;
    
    const { scale } = state;
    const x = char.x * scale;
    const y = char.y * scale;
    const w = char.width * scale;
    const h = char.height * scale;
    
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(x, y, w, h);
}

function drawBbox(charIndex, fillColor, strokeColor, lineWidth) {
    const char = state.characters[charIndex];
    if (!char) return;
    
    const { scale } = state;
    const x = char.x * scale;
    const y = char.y * scale;
    const w = char.width * scale;
    const h = char.height * scale;
    
    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);
    
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = lineWidth;
    ctx.strokeRect(x, y, w, h);
}

function drawCentroidLine() {
    if (state.characters.length < 2) return;
    
    const { scale } = state;
    
    ctx.strokeStyle = COLORS.centroidLine;
    ctx.lineWidth = 3;
    ctx.setLineDash([8, 4]);
    ctx.beginPath();
    
    state.characters.forEach((char, idx) => {
        const cx = (char.x + char.width / 2) * scale;
        const cy = (char.y + char.height / 2) * scale;
        
        if (idx === 0) {
            ctx.moveTo(cx, cy);
        } else {
            ctx.lineTo(cx, cy);
        }
    });
    
    ctx.stroke();
    ctx.setLineDash([]);
}

// ===== UI更新 =====
function updateStatus() {
    const assigned = new Set(state.columns.flat());
    const remaining = state.characters.length - assigned.size;
    
    if (state.viewMode === 'segment') {
        const segments = (state.segments && state.segments.length > 0) ? state.segments : [];
        elements.statusColumnsLabel.textContent = 'セグメント数:';
        elements.statusColumns.textContent = segments.length;
    } else {
        elements.statusColumnsLabel.textContent = '列数:';
        elements.statusColumns.textContent = state.columns.length;
    }

    elements.statusRemaining.textContent = remaining;
    
    if (state.characters.length > 0) {
        const char = state.characters[state.selectedIndex];
        const glyph = unicodeToChar(char.unicode);
        const glyphLabel = glyph ? `「${glyph}」` : '';
        const colIdx = getCharacterColumnIndex(state.selectedIndex);
        const colInfo = colIdx >= 0 ? ` (列${colIdx + 1})` : ' (未割当)';

        if (state.viewMode === 'segment' && colIdx >= 0) {
            const hit = getSegmentIndexForColumn(colIdx);
            const segIdx = hit ? hit.segIdx : -1;
            const segId = segIdx >= 0 ? (state.segmentIds[segIdx] || '-') : '-';
            elements.statusCurrent.textContent = `${char.char_id}${colInfo} (Seg:${segId}) ${glyphLabel}`.trim();
        } else {
            elements.statusCurrent.textContent = `${char.char_id}${colInfo} ${glyphLabel}`.trim();
        }
    } else {
        elements.statusCurrent.textContent = '-';
    }
}

function updateButtons() {
    elements.btnUndo.disabled = state.historyIndex <= 0;
    elements.btnRedo.disabled = state.historyIndex >= state.history.length - 1;

    const currentPageIndex = state.pages.indexOf(state.pageId);
    elements.btnPrev.disabled = currentPageIndex <= 0;
    elements.btnNext.disabled = currentPageIndex >= state.pages.length - 1;

    const isSegmentMode = state.viewMode === 'segment';
    const hasUnassigned = hasUnassignedCharacters();
    const selectedIsAssigned = getCharacterColumnIndex(state.selectedIndex) !== -1;

    if (elements.btnConfirmColumn) {
        elements.btnConfirmColumn.disabled = isSegmentMode ? !canSplitSegmentAtSelection() : !hasUnassigned;
        elements.btnConfirmColumn.innerHTML = isSegmentMode
            ? '<span class="btn__icon">↲</span> Seg Split'
            : '<span class="btn__icon">✓</span> 列確定';
        elements.btnConfirmColumn.title = isSegmentMode ? 'セグメント分割 (Enter)' : '列確定 (Enter)';
    }
    if (elements.btnUnassign) {
        elements.btnUnassign.disabled = isSegmentMode ? !canJoinSegmentAtSelection() : !selectedIsAssigned;
        elements.btnUnassign.innerHTML = isSegmentMode
            ? '<span class="btn__icon">🔗</span> Seg Join'
            : '<span class="btn__icon">⊘</span> 未分割へ';
        elements.btnUnassign.title = isSegmentMode ? 'セグメント結合（前と結合）(N)' : '選択文字を未分割に戻す (N)';
    }

    // 保存ボタンのハイライト（どちらかがdirtyならハイライト）
    if (state.isDirty || state.isSegmentsDirty) {
        elements.btnSave.classList.remove('btn--muted');
    } else {
        elements.btnSave.classList.add('btn--muted');
    }
}

async function saveSegments() {
    if (state.columns.length === 0 || state.segments.length === 0) {
        showToast('保存するセグメントがありません', true);
        return;
    }
    if (state.isDirty) {
        showToast('列の未保存変更があります。先に列を保存してください。', true);
        return;
    }
    if (!state.hasOriginalAnnotations) {
        showToast('セグメント保存には先に列の保存が必要です', true);
        return;
    }

    showLoading(true);
    try {
        // 連番を期待する運用向け: rename を使っていない場合は自動で詰める
        renumberSegmentIdsIfAuto();

        const segmentsPayload = state.segments.map((segColIdxes, segIdx) => {
            const segId = state.segmentIds[segIdx] || `SEG${String(segIdx + 1).padStart(4, '0')}`;
            const columnIds = segColIdxes.map((colIdx) => state.columnIds[colIdx]).filter(Boolean);
            return { segment_id: segId, column_ids: columnIds };
        });

        const res = await fetch(`/api/books/${state.bookId}/pages/${state.pageId}/save_segments`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                book_id: state.bookId,
                page_id: state.pageId,
                segments: segmentsPayload,
            }),
        });

        const result = await res.json();
        if (res.ok && result.success) {
            state.isSegmentsDirty = false;
            // 保存後は「自動連番」運用へ戻す（必要なら再度renameする）
            state.hasCustomSegmentIds = false;
            showToast(result.message);
            updateButtons();
        } else {
            showToast(result.detail || 'セグメント保存に失敗しました', true);
        }
    } catch (error) {
        console.error('セグメント保存に失敗:', error);
        showToast('セグメント保存に失敗しました', true);
    } finally {
        showLoading(false);
    }
}

function showLoading(show) {
    elements.loading.classList.toggle('hidden', !show);
}

// ===== ページ移動 =====
async function navigatePage(direction) {
    // ナビゲーション中なら無視（連続キー入力防止）
    if (state.isNavigating) return;
    
    const currentIndex = state.pages.indexOf(state.pageId);
    const newIndex = currentIndex + direction;
    
    if (newIndex < 0 || newIndex >= state.pages.length) return;
    
    state.isNavigating = true;
    
    try {
        if (state.isDirty) {
            const result = await showSaveDialog();
            if (result === 'cancel') return;
            if (result === 'save') {
                await saveAnnotations();
            }
        }
        if (state.isSegmentsDirty) {
            const result = await showSaveDialog();
            if (result === 'cancel') return;
            if (result === 'save') {
                await saveSegments();
            }
        }
        
        const newPageId = state.pages[newIndex];
        elements.pageSelect.value = newPageId;
        await loadPageData(state.bookId, newPageId);
    } finally {
        state.isNavigating = false;
    }
}

function showSaveDialog() {
    return new Promise((resolve) => {
        elements.saveDialog.showModal();
        
        const cleanup = () => {
            elements.dialogCancel.removeEventListener('click', onCancel);
            elements.dialogDiscard.removeEventListener('click', onDiscard);
            elements.dialogSave.removeEventListener('click', onSave);
        };
        
        const onCancel = () => { cleanup(); elements.saveDialog.close(); resolve('cancel'); };
        const onDiscard = () => { cleanup(); elements.saveDialog.close(); resolve('discard'); };
        const onSave = () => { cleanup(); elements.saveDialog.close(); resolve('save'); };
        
        elements.dialogCancel.addEventListener('click', onCancel);
        elements.dialogDiscard.addEventListener('click', onDiscard);
        elements.dialogSave.addEventListener('click', onSave);
    });
}

function showHelp() {
    elements.helpDialog.showModal();
}

// ===== イベントリスナー =====
function setupEventListeners() {
    // 書籍選択
    elements.bookSelect.addEventListener('change', async (e) => {
        const bookId = e.target.value;
        if (bookId) {
            // 未保存の変更があれば確認
            if (state.isDirty || state.isSegmentsDirty) {
                const result = await showSaveDialog();
                if (result === 'cancel') {
                    elements.bookSelect.value = state.bookId;
                    return;
                }
                if (result === 'save') {
                    if (state.isDirty) {
                        await saveAnnotations();
                    }
                    if (state.isSegmentsDirty) {
                        await saveSegments();
                    }
                }
            }
            
            await loadPages(bookId);
            
            // アノテーション済みページがあればその最後を、なければ最初のページを開く
            if (state.pages.length > 0) {
                const targetPageId = state.lastAnnotated || state.pages[0];
                elements.pageSelect.value = targetPageId;
                await loadPageData(bookId, targetPageId);
            }
        }
    });
    
    // ページ選択
    elements.pageSelect.addEventListener('change', async (e) => {
        const pageId = e.target.value;
        if (pageId && state.bookId) {
            if (state.isDirty || state.isSegmentsDirty) {
                const result = await showSaveDialog();
                if (result === 'cancel') {
                    elements.pageSelect.value = state.pageId;
                    return;
                }
                if (result === 'save') {
                    if (state.isDirty) {
                        await saveAnnotations();
                    }
                    if (state.isSegmentsDirty) {
                        await saveSegments();
                    }
                }
            }
            await loadPageData(elements.bookSelect.value, pageId);
        }
    });
    
        if (elements.viewToggleSeg) {
            elements.viewToggleSeg.addEventListener('change', () => {
                const nextMode = elements.viewToggleSeg.checked ? 'segment' : 'column';

                if (nextMode === 'segment') {
                    if (state.isDirty) {
                        showToast('列の未保存変更があります。先に保存してください。', true);
                        elements.viewToggleSeg.checked = false;
                        return;
                    }
                    if (!state.hasOriginalAnnotations) {
                        showToast('セグメント編集には先に列の保存が必要です', true);
                        elements.viewToggleSeg.checked = false;
                        return;
                    }
                }

                state.viewMode = nextMode;
                draw();
                updateStatus();
                updateButtons();
            });
        }

    // Autoトグル
    elements.autoToggle.addEventListener('change', () => {
        if (elements.autoToggle.checked) {
            // Auto ON: 未分割文字があれば自動推定を実行
            if (hasUnassignedCharacters()) {
                autoEstimateColumns();
                if (state.columns.length > 0) {
                    state.isDirty = true;
                }
                saveHistory();
            }
        } else {
            // Auto OFF: 確認なしで列をクリア（DBに元のアノテーションがない場合のみ）
            if (!state.hasOriginalAnnotations) {
                state.columns = [];
                state.isDirty = false;
                state.history = [{ columns: [], selectedIndex: state.selectedIndex }];
                state.historyIndex = 0;
            }
            // DBに元のアノテーションがある場合は何もしない（既存のアノテーションを維持）
        }
        draw();
        updateStatus();
        updateButtons();
    });
    
    // ボタン
    elements.btnPrev.addEventListener('click', () => navigatePage(-1));
    elements.btnNext.addEventListener('click', () => navigatePage(1));
    elements.btnUndo.addEventListener('click', undo);
    elements.btnRedo.addEventListener('click', redo);
    elements.btnSave.addEventListener('click', () => {
        if (state.viewMode === 'segment') {
            saveSegments();
        } else {
            saveAnnotations();
        }
    });
    elements.btnClear.addEventListener('click', clearAllAssignments);
    elements.btnHelp.addEventListener('click', showHelp);
    elements.helpClose.addEventListener('click', () => elements.helpDialog.close());

    if (elements.btnConfirmColumn) {
        elements.btnConfirmColumn.addEventListener('click', () => {
            if (state.viewMode === 'segment') {
                splitSegment();
            } else {
                splitColumn();
            }
        });
    }
    if (elements.btnUnassign) {
        elements.btnUnassign.addEventListener('click', () => {
            if (state.viewMode === 'segment') {
                joinSegment();
            } else {
                unassignCharacter();
            }
        });
    }
    
    // キーボード
    document.addEventListener('keydown', handleKeydown);

    const attachPressRepeat = (btn, delta) => {
        if (!btn) return;

        const INITIAL_DELAY_MS = 250;
        const REPEAT_MS = 60;

        let pointerId = null;
        let timeoutId = null;
        let intervalId = null;

        const clearTimers = () => {
            if (timeoutId) {
                clearTimeout(timeoutId);
                timeoutId = null;
            }
            if (intervalId) {
                clearInterval(intervalId);
                intervalId = null;
            }
        };

        const stop = () => {
            clearTimers();
            if (pointerId !== null) {
                try {
                    btn.releasePointerCapture(pointerId);
                } catch (error) {
                    console.debug('releasePointerCapture failed', error);
                }
                pointerId = null;
            }
        };

        const isInside = (clientX, clientY) => {
            const r = btn.getBoundingClientRect();
            return clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom;
        };

        btn.addEventListener('pointerdown', (e) => {
            if (btn.disabled) return;

            e.preventDefault();
            pointerId = e.pointerId;
            btn.setPointerCapture(pointerId);

            moveSelection(delta);

            timeoutId = setTimeout(() => {
                intervalId = setInterval(() => {
                    moveSelection(delta);
                }, REPEAT_MS);
            }, INITIAL_DELAY_MS);
        });

        btn.addEventListener('pointermove', (e) => {
            if (pointerId === null) return;
            if (e.pointerId !== pointerId) return;
            if (!isInside(e.clientX, e.clientY)) {
                stop();
            }
        });

        btn.addEventListener('pointerup', (e) => {
            if (pointerId === null) return;
            if (e.pointerId !== pointerId) return;
            stop();
        });

        btn.addEventListener('pointercancel', (e) => {
            if (pointerId === null) return;
            if (e.pointerId !== pointerId) return;
            stop();
        });

        btn.addEventListener('lostpointercapture', () => {
            stop();
        });
    };

    attachPressRepeat(elements.btnNavUp, -1);
    attachPressRepeat(elements.btnNavDown, 1);

    // キャンバスクリック
    elements.canvas.addEventListener('click', handleCanvasClick);
    
    // リサイズ
    window.addEventListener('resize', () => {
        if (state.image) {
            calculateScale();
            draw();
        }
    });
}

function handleKeydown(e) {
    // フォーカスがselect等にある場合は無視
    if (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;

    if (e.shiftKey && e.key.toLowerCase() === 'n') {
        e.preventDefault();
        clearAllAssignments();
        return;
    }
    
    switch (e.key) {
        case 'ArrowUp':
            e.preventDefault();
            moveSelection(-1);
            break;
            
        case 'ArrowDown':
            e.preventDefault();
            moveSelection(1);
            break;
            
        case 'ArrowLeft':
            e.preventDefault();
            navigatePage(-1);
            break;
            
        case 'ArrowRight':
            e.preventDefault();
            navigatePage(1);
            break;
            
        case 'Enter':
            e.preventDefault();
            if (state.viewMode === 'segment') {
                splitSegment();
            } else {
                splitColumn();
            }
            break;
            
        case 'z':
            e.preventDefault();
            undo();
            break;
            
        case 'y':
            e.preventDefault();
            redo();
            break;
            
        case 's':
            e.preventDefault();
            if (state.viewMode === 'segment') {
                saveSegments();
            } else {
                saveAnnotations();
            }
            break;
            
        case 'h':
            e.preventDefault();
            showHelp();
            break;
            
        case 'n':
            e.preventDefault();
            if (state.viewMode === 'segment') {
                joinSegment();
            } else {
                unassignCharacter();
            }
            break;

        case 'r':
            if (state.viewMode === 'segment') {
                e.preventDefault();
                renameSegment();
            }
            break;
            
        case 'a':
            e.preventDefault();
            // Autoトグルを切り替え（changeイベントを発火させる）
            elements.autoToggle.checked = !elements.autoToggle.checked;
            elements.autoToggle.dispatchEvent(new Event('change'));
            break;
    }
}

function moveSelection(delta) {
    if (state.characters.length === 0) return;

    const next = Math.max(0, Math.min(state.characters.length - 1, state.selectedIndex + delta));
    if (next === state.selectedIndex) return;

    state.selectedIndex = next;
    draw();
    updateStatus();
    updateButtons();
}

function handleCanvasClick(e) {
    const rect = elements.canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / state.scale;
    const y = (e.clientY - rect.top) / state.scale;

    for (let i = 0; i < state.characters.length; i++) {
        const char = state.characters[i];
        if (x >= char.x && x <= char.x + char.width &&
            y >= char.y && y <= char.y + char.height) {
            state.selectedIndex = i;
            draw();
            updateStatus();
            updateButtons();
            break;
        }
    }
}

// ===== 起動 =====
init();
