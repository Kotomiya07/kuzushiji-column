/**
 * くずし字列分割アノテーションツール - Frontend Application
 */

// ===== 状態管理 =====
const state = {
    // データ
    bookId: null,
    pageId: null,
    pages: [],
    lastAnnotated: null,  // 最後のアノテーション済みページ
    characters: [],
    imageWidth: 0,
    imageHeight: 0,
    
    // 選択状態
    selectedIndex: 0,
    
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
    btnSave: document.getElementById('btn-save'),
    btnClear: document.getElementById('btn-clear'),
    statusColumns: document.getElementById('status-columns'),
    statusCurrent: document.getElementById('status-current'),
    statusRemaining: document.getElementById('status-remaining'),
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
        state.lastAnnotated = data.last_annotated;  // 最後のアノテーション済みページ
        
        elements.pageSelect.innerHTML = '<option value="">ページを選択...</option>';
        data.pages.forEach(page => {
            const option = document.createElement('option');
            option.value = page;
            option.textContent = page;
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
            selectedIndex: state.selectedIndex,
        }];
        state.historyIndex = 0;
        
        // Auto分割した場合はdirtyとしてマーク（保存確認を出すため）
        state.isDirty = (!hasExistingAnnotations && elements.autoToggle.checked && state.columns.length > 0) || sandwichedRemoved;
        
        // 描画
        calculateScale();
        draw();
        updateStatus();
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
        state.columns.push(columnMap.get(colId));
    });
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
        selectedIndex: state.selectedIndex,
    });
    state.historyIndex = state.history.length - 1;
}

function undo() {
    // historyIndex > 0 なら前の状態に戻れる
    if (state.historyIndex > 0) {
        state.historyIndex--;
        const snapshot = state.history[state.historyIndex];
        state.columns = JSON.parse(JSON.stringify(snapshot.columns));
        state.selectedIndex = snapshot.selectedIndex;
        state.isDirty = state.historyIndex > 0; // 初期状態に戻ったらdirtyではない
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
        state.selectedIndex = snapshot.selectedIndex;
        state.isDirty = true;
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
    
    // 確定済み列のbboxを描画（列全体を囲む）
    state.columns.forEach((col, colIdx) => {
        const colorIdx = colIdx % COLORS.confirmed.length;
        drawColumnBbox(col, COLORS.confirmed[colorIdx], COLORS.confirmedStroke[colorIdx]);
    });
    
    // 全ての文字の枠線を描画（塗りつぶしなし）
    const assigned = new Set(state.columns.flat());
    state.characters.forEach((_, idx) => {
        if (idx !== state.selectedIndex) {
            // 確定済みか未割り当てかで色を変える
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

function drawColumnBbox(columnIndices, fillColor, strokeColor) {
    if (columnIndices.length === 0) return;
    
    const { scale } = state;
    
    // 列内の全文字のbboxを囲む最小矩形を計算
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    
    columnIndices.forEach(charIdx => {
        const char = state.characters[charIdx];
        if (char) {
            minX = Math.min(minX, char.x);
            minY = Math.min(minY, char.y);
            maxX = Math.max(maxX, char.x + char.width);
            maxY = Math.max(maxY, char.y + char.height);
        }
    });
    
    const x = minX * scale;
    const y = minY * scale;
    const w = (maxX - minX) * scale;
    const h = (maxY - minY) * scale;
    
    // 列bbox を塗りつぶし
    ctx.fillStyle = fillColor;
    ctx.fillRect(x, y, w, h);
    
    // 列bbox の枠線
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
    
    elements.statusColumns.textContent = state.columns.length;
    elements.statusRemaining.textContent = remaining;
    
    if (state.characters.length > 0) {
        const char = state.characters[state.selectedIndex];
        const colIdx = getCharacterColumnIndex(state.selectedIndex);
        const colInfo = colIdx >= 0 ? ` (列${colIdx + 1})` : ' (未割当)';
        elements.statusCurrent.textContent = `${char.char_id}${colInfo}`;
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
    
    // 保存ボタンのハイライト（isDirtyがtrueの時のみハイライト）
    if (state.isDirty) {
        elements.btnSave.classList.remove('btn--muted');
    } else {
        elements.btnSave.classList.add('btn--muted');
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
            if (state.isDirty) {
                const result = await showSaveDialog();
                if (result === 'cancel') {
                    elements.bookSelect.value = state.bookId;
                    return;
                }
                if (result === 'save') {
                    await saveAnnotations();
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
            if (state.isDirty) {
                const result = await showSaveDialog();
                if (result === 'cancel') {
                    elements.pageSelect.value = state.pageId;
                    return;
                }
                if (result === 'save') {
                    await saveAnnotations();
                }
            }
            await loadPageData(elements.bookSelect.value, pageId);
        }
    });
    
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
    elements.btnSave.addEventListener('click', saveAnnotations);
    elements.btnClear.addEventListener('click', clearAllAssignments);
    elements.btnHelp.addEventListener('click', showHelp);
    elements.helpClose.addEventListener('click', () => elements.helpDialog.close());
    
    // キーボード
    document.addEventListener('keydown', handleKeydown);
    
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
            if (state.selectedIndex > 0) {
                state.selectedIndex--;
                draw();
                updateStatus();
            }
            break;
            
        case 'ArrowDown':
            e.preventDefault();
            if (state.selectedIndex < state.characters.length - 1) {
                state.selectedIndex++;
                draw();
                updateStatus();
            }
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
            splitColumn();
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
            saveAnnotations();
            break;
            
        case 'h':
            e.preventDefault();
            showHelp();
            break;
            
        case 'n':
            e.preventDefault();
            unassignCharacter();
            break;
            
        case 'a':
            e.preventDefault();
            // Autoトグルを切り替え（changeイベントを発火させる）
            elements.autoToggle.checked = !elements.autoToggle.checked;
            elements.autoToggle.dispatchEvent(new Event('change'));
            break;
    }
}

function handleCanvasClick(e) {
    const rect = elements.canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / state.scale;
    const y = (e.clientY - rect.top) / state.scale;
    
    // クリック位置の文字を探す
    for (let i = 0; i < state.characters.length; i++) {
        const char = state.characters[i];
        if (x >= char.x && x <= char.x + char.width &&
            y >= char.y && y <= char.y + char.height) {
            state.selectedIndex = i;
            draw();
            updateStatus();
            break;
        }
    }
}

// ===== 起動 =====
init();
