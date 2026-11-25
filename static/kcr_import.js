"use strict";

const state = {
    pages: [],
    page: null,
    detail: null,
    drafts: new Map(),
    cards: [],
    selected: null,
    displayImage: null,
    originalImage: null,
    busy: false,
};
const elements = Object.fromEntries(
    ["book", "only-pending", "only-related", "pages", "list-message", "prev", "next",
        "next-pending", "home", "save", "progress-count", "progress-pages", "stage-meta",
        "stage-message", "stage-fit", "canvas", "cards", "page-summary", "feedback"]
        .map(name => [name, document.getElementById(`kcr-${name}`)]),
);
const STATUS_LABELS = { new: "新規", conflict_user: "重複", near: "近接" };
const ACTIONS = {
    add: "追加する",
    reject: "却下",
    keep_existing: "自分の箱を残す",
    replace: "KCRの箱に差し替える",
    keep_both: "両方残す",
};
const COLOR_TOKENS = {
    new: "--review-new", conflict_user: "--review-conflict", near: "--review-near",
    match: "--review-match", existing: "--review-existing",
};
const theme = getComputedStyle(document.body);
const colors = Object.fromEntries(
    Object.entries(COLOR_TOKENS).map(([key, token]) => [key, theme.getPropertyValue(token).trim()]),
);

// ===== データとページ移動 =====
function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

async function requestJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
        throw new Error(typeof data?.detail === "string" ? data.detail : `通信エラー (${response.status})`);
    }
    return data;
}

function pagePath(page) {
    return `books/${encodeURIComponent(page.book_id)}/pages/${encodeURIComponent(page.page_id)}`;
}

function visiblePages() {
    return state.pages.filter(page =>
        (!elements.book.value || page.book_id === elements.book.value) &&
        (!elements["only-pending"].checked || page.undecided_count > 0) &&
        (!elements["only-related"].checked || page.status_counts.conflict_user + page.status_counts.near > 0),
    );
}

function renderPages() {
    const pages = visiblePages();
    const list = elements.pages;
    list.replaceChildren();
    for (const page of pages) {
        const item = node("li");
        const button = node("button", "kcr-page-button");
        const current = state.page && pagePath(page) === pagePath(state.page);
        button.type = "button";
        button.disabled = state.busy;
        button.dataset.path = pagePath(page);
        button.dataset.complete = String(!page.undecided_count);
        if (current) button.setAttribute("aria-current", "page");
        button.append(
            node("span", "kcr-id", `${page.book_id} / ${page.page_id}`),
            node("span", "kcr-meta", `${page.undecided_count ? "" : "✓ "}未 ${page.undecided_count} / 全 ${page.total_count}`),
        );
        const tags = node("span", "kcr-tags");
        for (const status of ["conflict_user", "near"]) {
            if (page.status_counts[status]) {
                tags.append(node("span", `kcr-tag kcr-${status}`, `${STATUS_LABELS[status]} ${page.status_counts[status]}`));
            }
        }
        if (tags.childElementCount) button.append(tags);
        button.addEventListener("click", async () => {
            await openPage(page);
            for (const candidate of list.querySelectorAll("button")) {
                if (candidate.dataset.path === button.dataset.path) candidate.focus({ preventScroll: true });
            }
        });
        item.append(button);
        list.append(item);
    }
    elements["list-message"].hidden = pages.length > 0;
    elements["list-message"].textContent = pages.length ? "" : (
        state.pages.length ? "表示条件に合うページがありません。絞り込みを変更してください。" : "候補ページがありません。"
    );
    syncControls();
}

async function refreshPages() {
    const { summary, books, pages } = await requestJson("/api/kcr-import/pages");
    state.pages = pages;
    const book = elements.book.value;
    elements.book.replaceChildren(new Option("すべての書籍", ""));
    for (const entry of books) {
        elements.book.append(new Option(`${entry.book_id} (未 ${entry.undecided_count})`, entry.book_id));
    }
    elements.book.value = books.some(entry => entry.book_id === book) ? book : "";
    elements["progress-count"].textContent = `未判定 ${summary.undecided_count} / ${summary.total_candidates}`;
    elements["progress-pages"].textContent = `残ページ ${summary.undecided_pages} / ${summary.total_pages}`;
    renderPages();
}

function destinations() {
    const pages = visiblePages();
    const index = state.page ? state.pages.findIndex(page => pagePath(page) === pagePath(state.page)) : -1;
    const earlier = pages.filter(page => state.pages.indexOf(page) < index);
    const later = pages.filter(page => state.pages.indexOf(page) > index);
    return {
        previous: earlier.at(-1),
        next: later[0],
        pending: later.find(page => page.undecided_count) || earlier.find(page => page.undecided_count),
    };
}

function edited() {
    return state.cards.some(({ candidate }) => {
        const draft = state.drafts.get(candidate.candidate_id);
        return draft.decision !== (candidate.default_decision || "") || draft.unicode !== "";
    });
}

function mayLeavePage() {
    return !state.busy && (!edited() || window.confirm("未保存の入力を破棄して移動しますか？"));
}

function stageMessage(text) {
    elements["stage-message"].textContent = text;
    elements["stage-message"].hidden = !text;
}

async function loadPage(page) {
    state.page = page;
    state.detail = null;
    state.displayImage = null;
    state.originalImage = null;
    state.selected = null;
    state.cards = [];
    state.drafts.clear();
    elements.cards.replaceChildren();
    elements.canvas.hidden = true;
    elements["stage-meta"].textContent = `${page.book_id} / ${page.page_id}`;
    stageMessage("ページを読み込み中…");
    renderPages();
    try {
        const path = pagePath(page);
        const detail = await requestJson(`/api/kcr-import/${path}`);
        state.detail = detail;
        renderCards();
        const image = new Image();
        const version = encodeURIComponent(detail.image_version);
        image.src = `/api/${path}/image?variant=display&v=${version}`;
        await image.decode();
        state.displayImage = image;
        stageMessage("");
        drawStage();
        drawPreviews();
        if (detail.candidates.length) {
            const original = new Image();
            original.src = `/api/${path}/image?variant=original&v=${version}`;
            original.decode().then(() => {
                if (state.detail === detail) {
                    state.originalImage = original;
                    drawPreviews();
                }
            }).catch(() => { /* 表示用画像で拡大プレビューを維持する。 */ });
        }
    } catch (error) {
        stageMessage(`ページを表示できません: ${error.message}`);
    }
}

async function openPage(page) {
    if (!page || !mayLeavePage()) return;
    state.busy = true;
    feedback("");
    try {
        await loadPage(page);
    } finally {
        state.busy = false;
        syncControls();
    }
}

// ===== 判定フォーム =====
function normalizeUnicode(value) {
    const input = value.trim();
    const code = /^[Uu]\+[0-9A-Fa-f]{4,6}$/.test(input) ? Number.parseInt(input.slice(2), 16)
        : [...input].length === 1 ? input.codePointAt(0) : null;
    if (code === null || code > 0x10ffff || (code >= 0xd800 && code <= 0xdfff)) return null;
    return `U+${code.toString(16).toUpperCase().padStart(4, "0")}`;
}

function glyph(code) {
    const normalized = normalizeUnicode(code);
    return normalized ? String.fromCodePoint(Number.parseInt(normalized.slice(2), 16)) : "";
}

function adding(decision) {
    return decision === "add" || decision === "keep_both";
}

function renderCards() {
    const detail = state.detail;
    const characters = new Map(detail.characters.map(character => [character.char_id, character]));
    for (const [index, candidate] of detail.candidates.entries()) {
        const id = candidate.candidate_id;
        const draft = { decision: candidate.default_decision || "", unicode: "" };
        state.drafts.set(id, draft);
        const card = node("article", `kcr-card kcr-${candidate.status}`);
        const heading = node("h3", "kcr-card-title");
        const selectCard = node("button", "btn btn--outline", `#${index + 1}`);
        selectCard.type = "button";
        selectCard.setAttribute("aria-label", `候補 ${index + 1} を画像上で強調`);
        selectCard.setAttribute("aria-pressed", "false");
        selectCard.addEventListener("click", () => chooseCandidate(id));
        heading.append(selectCard, node("span", "kcr-tag", STATUS_LABELS[candidate.status]), node("span", "kcr-meta", candidate.split));
        const crop = node("canvas", "kcr-crop");
        crop.setAttribute("aria-label", `候補 ${index + 1} 周辺の拡大画像`);
        card.append(heading, crop, node("p", "kcr-id", `[${candidate.box.join(", ")}]`));
        const character = characters.get(candidate.resolved_match_char_id);
        if (candidate.match) {
            const { char_id: oldId, unicode } = candidate.match;
            card.append(node("p", "kcr-meta",
                `既存: ${character?.char_id || oldId} / ${unicode} ${glyph(unicode)} / IoU ${candidate.match_iou.toFixed(2)}`,
            ));
        }
        if (candidate.match_warning) card.append(node("p", "kcr-error", candidate.match_warning));
        if (!candidate.in_corrected_list) card.append(node("p", "kcr-error", "補正版一覧には含まれていません。"));

        const action = node("select");
        const options = candidate.status === "conflict_user"
            ? ["keep_existing", "replace", "keep_both", ""] : ["", "add", "reject"];
        for (const key of options) {
            const option = new Option(ACTIONS[key] || "未判定", key);
            option.disabled = key === "replace" && !candidate.resolved_match_char_id;
            action.append(option);
        }
        action.value = draft.decision;
        const actionLabel = node("label", "kcr-field", `候補 ${index + 1} の判定`);
        actionLabel.append(action);
        const input = node("input");
        input.type = "text";
        input.placeholder = "例: U+3042 または あ";
        input.autocomplete = "off";
        input.spellcheck = false;
        const hint = node("small");
        hint.id = `kcr-unicode-hint-${index}`;
        input.setAttribute("aria-describedby", hint.id);
        const unicodeLabel = node("label", "kcr-field", "Unicode（必須）");
        unicodeLabel.append(input, hint);
        card.append(actionLabel, unicodeLabel);

        function changed() {
            draft.decision = action.value;
            draft.unicode = input.value;
            if (elements.feedback.dataset.error === "true") feedback("");
            chooseCandidate(id);
            syncControls();
        }
        action.addEventListener("change", changed);
        input.addEventListener("input", changed);
        elements.cards.append(card);
        state.cards.push({
            candidate, card, selectCard, crop, action, input, hint, unicodeLabel,
            matchBox: character ? characterBox(character) : candidate.match?.box,
        });
    }
    if (!state.cards.length) {
        elements.cards.append(node("p", "kcr-note", "このページの候補は判定済みです。次のページを選んでください。"));
    } else {
        chooseCandidate(state.cards[0].candidate.candidate_id);
    }
    syncControls();
}

function chooseCandidate(id, scroll = false) {
    state.selected = id;
    for (const entry of state.cards) {
        const selected = entry.candidate.candidate_id === id;
        entry.card.dataset.selected = String(selected);
        entry.selectCard.setAttribute("aria-pressed", String(selected));
        if (selected && scroll) entry.card.scrollIntoView({ block: "nearest" });
    }
    drawStage();
}

function getDecisions() {
    return [...state.drafts].flatMap(([id, draft]) => draft.decision ? [{
        candidate_id: id, decision: draft.decision, unicode: adding(draft.decision) ? normalizeUnicode(draft.unicode) : null,
    }] : []);
}

function feedback(message, error = false) {
    elements.feedback.textContent = message;
    elements.feedback.dataset.error = String(error);
}

function syncControls() {
    const decisions = getDecisions();
    const invalid = decisions.filter(item => adding(item.decision) && !item.unicode).length;
    for (const { candidate, action, input, unicodeLabel, hint } of state.cards) {
        const draft = state.drafts.get(candidate.candidate_id);
        const needsUnicode = adding(draft.decision);
        const code = normalizeUnicode(draft.unicode);
        action.disabled = state.busy || !state.displayImage;
        input.disabled = action.disabled;
        input.required = needsUnicode;
        input.setAttribute("aria-invalid", String(needsUnicode && !code));
        unicodeLabel.hidden = !needsUnicode;
        hint.dataset.error = String(!code);
        hint.textContent = code ? `${glyph(code)} → ${code}` : "U+XXXX 形式または1文字を入力してください。";
    }
    const { previous, next, pending } = destinations();
    elements.prev.disabled = state.busy || !previous;
    elements.next.disabled = state.busy || !next;
    elements["next-pending"].disabled = state.busy || !pending;
    elements.pages.querySelectorAll("button").forEach(button => { button.disabled = state.busy; });
    elements.save.disabled = state.busy || !state.displayImage || !decisions.length || invalid > 0;
    elements.save.textContent = `保存 (${decisions.length})`;
    if (invalid) feedback(`${invalid}件の候補に有効な Unicode を入力してください。`, true);
    if (state.detail) {
        const additions = decisions.filter(item => adding(item.decision)).length;
        const replacements = decisions.filter(item => item.decision === "replace").length;
        const rejections = decisions.length - additions - replacements;
        elements["page-summary"].textContent =
            `追加 ${additions} / 差替 ${replacements} / 却下 ${rejections} / 未判定 ${state.cards.length - decisions.length}`;
    } else {
        elements["page-summary"].textContent = "";
    }
}

async function savePage() {
    if (elements.save.disabled) return;
    const page = state.page;
    const decisions = getDecisions();
    state.busy = true;
    feedback("保存中…");
    syncControls();
    try {
        const result = await requestJson(`/api/kcr-import/${pagePath(page)}/save`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decisions }),
        });
        await loadPage(page);
        await refreshPages();
        feedback(`${result.saved_count}件保存しました。残り ${result.remaining_undecided_count}件。`);
    } catch (error) {
        feedback(`保存または表示の更新に失敗しました: ${error.message}`, true);
    } finally {
        state.busy = false;
        syncControls();
    }
}

// ===== ページ画像・候補の枠・拡大プレビュー =====
function characterBox({ x, y, width, height }) {
    return [x, y, width, height];
}

function stylizeBox(ctx, [x, y, w, h], color, width, dashed = false, fill = false) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    if (dashed) ctx.setLineDash([5, 3]);
    if (fill) {
        ctx.save();
        ctx.globalAlpha *= 0.15;
        ctx.fillStyle = color;
        ctx.fillRect(x, y, w, h);
        ctx.restore();
    }
    ctx.strokeRect(x, y, w, h);
    ctx.restore();
}

function badge(ctx, text, [x, y], color, canvasWidth) {
    ctx.save();
    ctx.font = "600 11px sans-serif";
    const width = ctx.measureText(text).width + 8;
    const left = Math.max(0, Math.min(x, canvasWidth - width));
    const top = Math.max(0, y - 15);
    ctx.fillStyle = color;
    ctx.fillRect(left, top, width, 15);
    ctx.fillStyle = "#fff";
    ctx.textBaseline = "middle";
    ctx.fillText(text, left + 4, top + 7.5);
    ctx.restore();
}

function canvasContext(canvas, width, height) {
    const density = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.max(1, Math.round(width * density));
    canvas.height = Math.max(1, Math.round(height * density));
    const ctx = canvas.getContext("2d");
    ctx.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
    return ctx;
}

function drawStage() {
    if (!state.displayImage || !state.detail) return;
    const canvas = elements.canvas;
    const host = elements["stage-fit"];
    const { image_width: width, image_height: height, characters } = state.detail;
    const scale = Math.min((host.clientWidth - 28) / width, (host.clientHeight - 28) / height);
    if (scale <= 0) return;
    const dw = width * scale;
    const dh = height * scale;
    canvas.style.width = `${dw}px`;
    canvas.style.height = `${dh}px`;
    canvas.hidden = false;
    const ctx = canvasContext(canvas, dw, dh);
    ctx.drawImage(state.displayImage, 0, 0, dw, dh);
    const scaleBox = box => box.map(value => value * scale);
    for (const character of characters) {
        stylizeBox(ctx, scaleBox(characterBox(character)), colors.existing, 0.8);
    }
    for (const { matchBox } of state.cards) {
        if (matchBox) stylizeBox(ctx, scaleBox(matchBox), colors.match, 2, true);
    }
    const selectedIndex = state.cards.findIndex(({ candidate }) => candidate.candidate_id === state.selected);
    const order = state.cards.map((_, i) => i).filter(i => i !== selectedIndex);
    if (selectedIndex >= 0) order.push(selectedIndex);
    for (const index of order) {
        const { candidate, matchBox } = state.cards[index];
        const draft = state.drafts.get(candidate.candidate_id);
        const selected = index === selectedIndex;
        const rejected = ["keep_existing", "reject"].includes(draft.decision);
        const color = colors[candidate.status];
        const box = scaleBox(candidate.box);
        const [x, y, w, h] = box;
        if (selected) {
            stylizeBox(ctx, [x - 2, y - 2, w + 4, h + 4], "#fff", 2);
            if (matchBox) {
                const [mx, my, mw, mh] = scaleBox(matchBox);
                ctx.beginPath();
                ctx.moveTo(x + w / 2, y + h / 2);
                ctx.lineTo(mx + mw / 2, my + mh / 2);
                ctx.strokeStyle = colors.match;
                ctx.lineWidth = 1.5;
                ctx.stroke();
                stylizeBox(ctx, [mx, my, mw, mh], colors.match, 2.6, true);
                const matchId = candidate.resolved_match_char_id || candidate.match.char_id;
                const matchCode = candidate.match.unicode;
                badge(ctx, `既存 ${matchId} (${matchCode})`, [mx + mw, Math.min(my + mh + 16, dh)], colors.match, dw);
            }
        }
        ctx.globalAlpha = rejected && !selected ? 0.65 : 1;
        stylizeBox(ctx, box, color, selected ? 2.8 : 1.8, rejected, !rejected);
        ctx.globalAlpha = 1;
        const character = adding(draft.decision) ? glyph(draft.unicode) : "";
        if (selected || character) badge(ctx, `#${index + 1}${selected ? " KCR" : ""}${character ? ` ${character}` : ""}`, box, color, dw);
    }
}

function drawPreviews() {
    const image = state.originalImage || state.displayImage;
    if (!image || !state.detail) return;
    const { image_width: iw, image_height: ih } = state.detail;
    for (const { candidate, matchBox, crop } of state.cards) {
        const [x, y, w, h] = candidate.box;
        const [mx, my, mw, mh] = matchBox || candidate.box;
        const pad = Math.max(w, h, mw, mh) * 0.25;
        const left = Math.max(0, Math.min(x, mx) - pad);
        const top = Math.max(0, Math.min(y, my) - pad);
        const width = Math.min(iw, Math.max(x + w, mx + mw) + pad) - left;
        const height = Math.min(ih, Math.max(y + h, my + mh) + pad) - top;
        if (width <= 0 || height <= 0 || !crop.clientWidth || !crop.clientHeight) continue;
        const ctx = canvasContext(crop, crop.clientWidth, crop.clientHeight);
        const scale = Math.min(crop.clientWidth / width, crop.clientHeight / height);
        const offsetX = (crop.clientWidth - width * scale) / 2;
        const offsetY = (crop.clientHeight - height * scale) / 2;
        const sx = image.naturalWidth / iw;
        const sy = image.naturalHeight / ih;
        ctx.drawImage(image, left * sx, top * sy, width * sx, height * sy, offsetX, offsetY, width * scale, height * scale);
        const scaled = (bx, by, bw, bh) => [
            offsetX + (bx - left) * scale, offsetY + (by - top) * scale, bw * scale, bh * scale,
        ];
        if (matchBox) stylizeBox(ctx, scaled(mx, my, mw, mh), colors.match, 2, true);
        stylizeBox(ctx, scaled(x, y, w, h), colors[candidate.status], 2);
    }
}

elements.canvas.addEventListener("click", event => {
    if (!state.detail) return;
    const rect = elements.canvas.getBoundingClientRect();
    const x = (event.clientX - rect.left) * state.detail.image_width / rect.width;
    const y = (event.clientY - rect.top) * state.detail.image_height / rect.height;
    const hits = state.cards.filter(({ candidate }) => {
        const [bx, by, w, h] = candidate.box;
        return bx <= x && x <= bx + w && by <= y && y <= by + h;
    });
    if (hits.length) {
        const index = hits.findIndex(({ candidate }) => candidate.candidate_id === state.selected);
        chooseCandidate(hits[(index + 1) % hits.length].candidate.candidate_id, true);
    }
});
new ResizeObserver(() => { drawStage(); drawPreviews(); }).observe(elements["stage-fit"]);

elements.prev.addEventListener("click", () => openPage(destinations().previous));
elements.next.addEventListener("click", () => openPage(destinations().next));
elements["next-pending"].addEventListener("click", () => openPage(destinations().pending));
for (const name of ["book", "only-pending", "only-related"]) {
    elements[name].addEventListener("change", renderPages);
}
elements.save.addEventListener("click", savePage);
elements.home.addEventListener("click", event => {
    if (state.busy) event.preventDefault();
});
window.addEventListener("beforeunload", event => {
    if (!edited() && !state.busy) return;
    event.preventDefault();
    event.returnValue = "";
});

refreshPages().then(() => openPage(visiblePages()[0])).catch(error => {
    elements["list-message"].textContent = `候補一覧を読み込めません: ${error.message}`;
    elements["list-message"].hidden = false;
});
