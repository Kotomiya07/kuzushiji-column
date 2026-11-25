"""KCR 候補の確認と既存アノテーションへの取り込み。"""

import csv
import json
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import groupby
from pathlib import Path
from statistics import mean, median
from threading import Lock
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, TypeAdapter

KCR_DIR = Path("kcr_import")
CANDIDATES_PATH = KCR_DIR / "candidates.json"
DECISIONS_PATH = KCR_DIR / "decisions.jsonl"
BACKUPS_DIR = KCR_DIR / "backups"
BOX_COLUMNS = ("X", "Y", "Width", "Height")
COORDINATE_COLUMNS = (
    "Unicode",
    "Image",
    "X",
    "Y",
    "Block ID",
    "Char ID",
    "Width",
    "Height",
)
ADD_DECISIONS = {"add", "keep_both"}
STATUSES = ("new", "conflict_user", "near")
Row = dict[str, str]
Box = tuple[int, int, int, int]
Character = tuple[str, str, Box]
MatchAnnotations = Callable[[list[Row], list[Row]], dict[str, Row]]
Decision = Literal["add", "reject", "keep_existing", "replace", "keep_both"]
SAVE_LOCK = Lock()


class KcrDecisionInput(BaseModel):
    candidate_id: str
    decision: Decision
    unicode: str | None = None


class KcrSaveRequest(BaseModel):
    decisions: list[KcrDecisionInput]


def make_candidate_id(book: str, page: str, box: Box) -> str:
    """書籍・ページ・元の候補座標から一意キーを作る。"""
    return f"{book}:{page}:{','.join(map(str, box))}"


class CandidateMatch(BaseModel):
    char_id: str
    unicode: str
    box: Box
    user_added: bool = False


class Candidate(BaseModel):
    book: str
    page: str
    split: str
    box: Box
    status: Literal["new", "conflict_user", "near"]
    match: CandidateMatch | None
    match_iou: float
    in_corrected_list: bool

    @property
    def candidate_id(self) -> str:
        """入力やログの行順に依存しないキーを返す。"""
        return make_candidate_id(self.book, self.page, self.box)


class DecisionKey(BaseModel):
    book: str
    page: str
    box: Box


@dataclass(frozen=True)
class DataDirs:
    raw: Path
    raw_csv: Path
    output: Path
    output_seg: Path


def normalize_unicode_input(raw_value: str | None) -> str:
    """単一文字または U+ 表記を正規化し、不正なコードポイントを拒否する。"""
    value = (raw_value or "").strip()
    if re.fullmatch(r"[Uu]\+[0-9A-Fa-f]{4,6}", value):
        codepoint = int(value[2:], 16)
    elif len(value) == 1:
        codepoint = ord(value)
    else:
        raise ValueError("Unicode を U+XXXX 形式または1文字で入力してください。")
    if codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
        raise ValueError("有効な Unicode コードポイントを入力してください。")
    return f"U+{codepoint:04X}"


def load_candidates() -> list[Candidate]:
    """利用可能な候補を入力ファイルから読み込む。"""
    if not CANDIDATES_PATH.exists():
        raise HTTPException(404, "KCR 候補ファイルが見つかりません。")
    return TypeAdapter(list[Candidate]).validate_json(CANDIDATES_PATH.read_bytes())


def load_decided_keys() -> set[str]:
    """判定ログの内容を再読み込みし、保存済みの候補キーを返す。"""
    if not DECISIONS_PATH.exists():
        return set()
    decided: set[str] = set()
    with DECISIONS_PATH.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                entry = DecisionKey.model_validate_json(line)
                decided.add(make_candidate_id(entry.book, entry.page, entry.box))
    return decided


def list_candidate_pages(
    book_id: str | None, only_undecided: bool
) -> dict[str, object]:
    """書籍・ページごとの候補数と全体進捗を返す。"""
    groups: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for candidate in load_candidates():
        groups[candidate.book, candidate.page].append(candidate)
    decided = load_decided_keys()
    books: dict[str, Counter[str]] = defaultdict(Counter)
    pages: list[dict[str, object]] = []
    total = remaining = remaining_pages = 0
    for (book, page), candidates in sorted(groups.items()):
        count = len(candidates)
        undecided = sum(
            candidate.candidate_id not in decided for candidate in candidates
        )
        total += count
        remaining += undecided
        remaining_pages += bool(undecided)
        books[book].update(total_count=count, undecided_count=undecided)
        if (book_id is not None and book != book_id) or (
            only_undecided and not undecided
        ):
            continue
        statuses = Counter(candidate.status for candidate in candidates)
        pages.append(
            {
                "book_id": book,
                "page_id": page,
                "total_count": count,
                "undecided_count": undecided,
                "decided_count": count - undecided,
                "status_counts": {status: statuses[status] for status in STATUSES},
            }
        )
    return {
        "summary": {
            "total_candidates": total,
            "decided_count": total - remaining,
            "undecided_count": remaining,
            "total_pages": len(groups),
            "undecided_pages": remaining_pages,
        },
        "books": [{"book_id": book, **counts} for book, counts in books.items()],
        "pages": pages,
    }


def page_candidates(book_id: str, page_id: str) -> list[Candidate]:
    """候補が登録されたページだけを取り込み対象とする。"""
    candidates = [
        item
        for item in load_candidates()
        if (item.book, item.page) == (book_id, page_id)
    ]
    if not candidates:
        raise HTTPException(404, "KCR 候補のあるページが見つかりません。")
    return candidates


def box_iou(a: Box, b: Box) -> float:
    """2つの箱の IoU を返す。"""
    x, y, w, h = a
    u, v, s, t = b
    intersection = max(0, min(x + w, u + s) - max(x, u)) * max(
        0, min(y + h, v + t) - max(y, v)
    )
    return intersection / (w * h + s * t - intersection) if intersection else 0.0


def resolve_match(characters: list[Character], match: CandidateMatch) -> str | None:
    """ID・Unicode・IoU を照合し、対象が一意の場合のみ現在の ID を返す。"""
    scores = [
        (char_id, box_iou(box, match.box))
        for char_id, code, box in characters
        if code == match.unicode
    ]
    direct = [
        char_id
        for char_id, score in scores
        if char_id == match.char_id and score >= 0.5
    ]
    if len(direct) == 1:
        return direct[0]
    nearby = [char_id for char_id, score in scores if score >= 0.7]
    return nearby[0] if len(nearby) == 1 else None


def candidate_page_fields(
    candidates: list[Candidate],
    characters: list[Character],
    output_path: Path,
    page_id: str,
) -> dict[str, object]:
    """画面表示向けに、未判定候補と照合結果を組み立てる。"""
    decided = load_decided_keys()
    pending = []
    for candidate in candidates:
        if candidate.candidate_id in decided:
            continue
        match = candidate.match
        resolved = resolve_match(characters, match) if match else None
        warning = None
        if match and resolved is None:
            warning = "現在の既存文字を特定できません。表示枠は候補データに記録された座標です。"
        elif match and resolved != match.char_id:
            warning = (
                f"保存時点の ID {match.char_id} は現在の {resolved} に対応します。"
            )
        pending.append(
            {
                **candidate.model_dump(),
                "candidate_id": candidate.candidate_id,
                "default_decision": "keep_existing"
                if candidate.status == "conflict_user"
                else None,
                "resolved_match_char_id": resolved,
                "match_warning": warning,
            }
        )
    has_output = False
    if output_path.exists():
        with output_path.open(encoding="utf-8-sig", newline="") as stream:
            has_output = any(row["Image"] == page_id for row in csv.DictReader(stream))
    return {
        "candidates": pending,
        "total_count": len(candidates),
        "undecided_count": len(pending),
        "decided_count": len(candidates) - len(pending),
        "has_output_page": has_output,
    }


@dataclass
class CsvTable:
    fields: list[str]
    rows: list[Row]

    @classmethod
    def read(cls, path: Path) -> "CsvTable":
        """列順や空欄を保持して CSV を読む。"""
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or not set(COORDINATE_COLUMNS).issubset(
                reader.fieldnames
            ):
                raise HTTPException(409, f"座標 CSV の列形式が不正です: {path}")
            return cls(reader.fieldnames, list(reader))

    def page(self, page_id: str) -> list[Row]:
        """対象ページの行を取り出す。"""
        return [row for row in self.rows if row["Image"] == page_id]

    def with_page(self, page_id: str, updated: list[Row]) -> "CsvTable":
        """元ページの位置で行を置き換え、他ページの行順を維持する。"""
        rows: list[Row] = []
        inserted = False
        for row in self.rows:
            if row["Image"] != page_id:
                rows.append(row)
            elif not inserted:
                rows.extend(updated)
                inserted = True
        if not inserted:
            rows.extend(updated)
        return CsvTable(self.fields, rows)


def row_box(row: Row) -> Box:
    """CSV の座標列を箱に変換する。"""
    return (
        int(float(row["X"])),
        int(float(row["Y"])),
        int(float(row["Width"])),
        int(float(row["Height"])),
    )


@dataclass
class PageRecord:
    coordinates: Row
    old_char_id: str | None = None
    annotations: dict[str, Row] = field(default_factory=dict)

    @property
    def box(self) -> Box:
        return row_box(self.coordinates)

    @property
    def center_x(self) -> float:
        x, _, w, _ = self.box
        return x + w / 2

    @property
    def y_key(self) -> tuple[float, int]:
        _, y, _, h = self.box
        return y + h / 2, y

    @property
    def column_id(self) -> str:
        return self.annotations.get("output", {}).get("Column ID", "").strip()

    @property
    def wari_id(self) -> str:
        return self.annotations.get("output", {}).get("Wari ID", "").strip()


@dataclass
class ReadingColumn:
    members: list[PageRecord]
    column_id: str = ""
    additions: list[PageRecord] = field(default_factory=list)

    @property
    def center(self) -> float:
        return median(record.center_x for record in self.members or self.additions)

    def accepts(self, record: PageRecord, threshold: float) -> bool:
        """既存の列は X 範囲と文字幅、仮列は中心距離で判定する。"""
        if not self.column_id:
            return abs(self.center - record.center_x) <= threshold
        x, _, w, _ = record.box
        left = min(member.box[0] for member in self.members)
        right = max(member.box[0] + member.box[2] for member in self.members)
        return (x < right and x + w > left) or abs(
            self.center - record.center_x
        ) <= max(
            threshold,
            (right - left) * 0.6,
        )

    def ordered(self) -> list[PageRecord]:
        """既存列の相対順を残し、割注ブロックを分断せずに新規文字を挿入する。"""
        if not self.column_id:
            return sorted(
                self.members + self.additions, key=lambda record: record.y_key
            )
        pending = sorted(self.additions, key=lambda record: record.y_key)
        result: list[PageRecord] = []
        index = 0
        for _, records in groupby(
            self.members, key=lambda record: record.wari_id or record.old_char_id
        ):
            block = list(records)
            keys = [record.y_key for record in block]
            # 割注は箱群の中央と比較し、右行と左行の間には挿入しない。
            block_key = ((min(keys)[0] + max(keys)[0]) / 2, min(keys)[1])
            while index < len(pending) and pending[index].y_key < block_key:
                result.append(pending[index])
                index += 1
            result.extend(block)
        return result + pending[index:]


def estimate_columns_from_x(
    records: list[PageRecord], threshold: float
) -> list[ReadingColumn]:
    """右から左へ、隣接する中心 X の間隔を基準に列を推定する。"""
    columns: list[ReadingColumn] = []
    previous_x = float("inf")
    for record in sorted(records, key=lambda item: -item.center_x):
        if not columns or previous_x - record.center_x > threshold:
            columns.append(ReadingColumn([]))
        columns[-1].members.append(record)
        previous_x = record.center_x
    return columns


def common_block(rows: list[Row], fallback: str) -> str:
    """最頻出の空欄でない Block ID を取得する。"""
    counts = Counter(row["Block ID"] for row in rows if row.get("Block ID", "").strip())
    return counts.most_common(1)[0][0] if counts else fallback


def order_with_additions(
    existing: list[PageRecord],
    additions: list[PageRecord],
    fallback_block: str,
) -> list[PageRecord]:
    """新規文字を所属列へ挿入し、列順と列内順を確定する。"""
    threshold = mean(record.box[2] for record in existing or additions) * 0.8
    assigned: dict[str, list[PageRecord]] = defaultdict(list)
    unassigned: list[PageRecord] = []
    for record in sorted(
        existing, key=lambda item: int(item.coordinates["Char ID"][1:])
    ):
        if record.column_id:
            assigned[record.column_id].append(record)
        else:
            unassigned.append(record)
    columns = [ReadingColumn(assigned[col_id], col_id) for col_id in sorted(assigned)]
    if not columns:
        columns = estimate_columns_from_x(existing, threshold)
    else:
        loose: list[PageRecord] = []
        for record in unassigned:
            closest = min(columns, key=lambda col: abs(col.center - record.center_x))
            if abs(closest.center - record.center_x) <= threshold:
                closest.members.append(record)
            else:
                loose.append(record)
        for column in columns:
            column.members.sort(key=lambda item: int(item.coordinates["Char ID"][1:]))
        columns.extend(estimate_columns_from_x(loose, threshold))

    if not existing:
        columns = estimate_columns_from_x(additions, threshold)
    else:
        for record in sorted(additions, key=lambda item: -item.center_x):
            possible = [col for col in columns if col.accepts(record, threshold)]
            if not possible:
                possible = [ReadingColumn([])]
                columns.extend(possible)
            closest = min(possible, key=lambda col: abs(col.center - record.center_x))
            closest.additions.append(record)

    if assigned:
        # 既存 Column ID の順を固定し、推定列だけを X 位置で差し込む。
        known = [col for col in columns if col.column_id]
        for col in sorted(
            (col for col in columns if not col.column_id), key=lambda item: -item.center
        ):
            index = next(
                (i for i, other in enumerate(known) if other.center < col.center),
                len(known),
            )
            known.insert(index, col)
        columns = known
    else:
        columns.sort(key=lambda col: -col.center)

    ordered: list[PageRecord] = []
    for column in columns:
        block = common_block(
            [record.coordinates for record in column.members], fallback_block
        )
        for record in column.ordered():
            if record.old_char_id is None:
                record.coordinates["Block ID"] = block
            ordered.append(record)
    return ordered


@dataclass(frozen=True)
class CsvUpdate:
    kind: str
    path: Path
    table: CsvTable


def stage_file(path: Path, write: Callable[[Path], None]) -> Path:
    """置換先と同じディレクトリに全内容を書き出す。"""
    with tempfile.NamedTemporaryFile(
        dir=path.parent, suffix=".tmp", delete=False
    ) as stream:
        temporary = Path(stream.name)
    try:
        write(temporary)
        if path.exists():
            shutil.copymode(path, temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def write_table(table: CsvTable, path: Path) -> None:
    """ヘッダー順を維持して座標・アノテーションを出力する。"""
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, table.fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(table.rows)


def persist_updates(
    updates: list[CsvUpdate],
    logs: list[dict[str, object]],
    stamp: datetime,
    book_id: str,
    page_id: str,
) -> list[str]:
    """全 CSV をバックアップしてから置換し、最後に判定ログを確定する。"""
    backups: dict[Path, Path] = {}
    if updates:
        folder = BACKUPS_DIR / f"{stamp:%Y%m%d_%H%M%S_%f}_{book_id}_{page_id}"
        folder.mkdir(parents=True)
        for update in updates:
            if update.path.exists():
                backup = folder / f"{update.kind}_{update.path.name}"
                shutil.copy2(update.path, backup)
                backups[update.path] = backup

    def append_logs(path: Path) -> None:
        """既存ログの末尾に新しい判定を追加したファイルを準備する。"""
        previous = DECISIONS_PATH.read_bytes() if DECISIONS_PATH.exists() else b""
        if previous and not previous.endswith(b"\n"):
            previous += b"\n"
        lines = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in logs)
        path.write_bytes(previous + lines.encode("utf-8"))

    staged: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for update in updates:
            staged[update.path] = stage_file(
                update.path,
                lambda path: write_table(update.table, path),
            )
        staged[DECISIONS_PATH] = stage_file(DECISIONS_PATH, append_logs)
        for path, temporary in staged.items():
            temporary.replace(path)
            replaced.append(path)
    except OSError:
        for path in reversed(replaced):
            if path in backups:
                restored = stage_file(
                    path, lambda temporary: shutil.copy2(backups[path], temporary)
                )
                try:
                    restored.replace(path)
                finally:
                    restored.unlink(missing_ok=True)
            else:
                path.unlink(missing_ok=True)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
    return [str(path) for path in backups.values()]


def apply_kcr_decisions(
    book_id: str,
    page_id: str,
    decisions: list[KcrDecisionInput],
    dirs: DataDirs,
    match_annotations: MatchAnnotations,
) -> dict[str, object]:
    """同時保存を直列化し、検証済みのページ内判定だけを確定する。"""
    with SAVE_LOCK:
        return _apply_kcr_decisions(
            book_id, page_id, decisions, dirs, match_annotations
        )


def _apply_kcr_decisions(
    book_id: str,
    page_id: str,
    decisions: list[KcrDecisionInput],
    dirs: DataDirs,
    match_annotations: MatchAnnotations,
) -> dict[str, object]:
    """候補検証、行編集、バックアップ付き書き込みを実行する。"""
    candidates = {
        candidate.candidate_id: candidate
        for candidate in page_candidates(book_id, page_id)
    }
    decided = load_decided_keys()
    if not decisions or len({item.candidate_id for item in decisions}) != len(
        decisions
    ):
        raise HTTPException(400, "判定が空、または同じ候補が重複しています。")
    validated: list[tuple[Candidate, Decision, str | None]] = []
    for item in decisions:
        candidate = candidates.get(item.candidate_id)
        if candidate is None:
            raise HTTPException(400, "このページにない候補が指定されました。")
        if item.candidate_id in decided:
            raise HTTPException(
                409,
                "すでに保存された候補が含まれています。ページを開き直してください。",
            )
        allowed = (
            {"keep_existing", "replace", "keep_both"}
            if candidate.status == "conflict_user"
            else {"add", "reject"}
        )
        if item.decision not in allowed:
            raise HTTPException(400, "候補の分類に対して許可されない判定です。")
        try:
            code = (
                normalize_unicode_input(item.unicode)
                if item.decision in ADD_DECISIONS
                else None
            )
        except ValueError as error:
            raise HTTPException(400, f"{candidate.candidate_id}: {error}") from error
        validated.append((candidate, item.decision, code))

    raw_path = dirs.raw / book_id / f"{book_id}_coordinate.csv"
    changes_csv = any(
        action in ADD_DECISIONS or action == "replace" for _, action, _ in validated
    )
    if changes_csv and not raw_path.exists():
        raise HTTPException(404, f"座標 CSV が見つかりません: {raw_path}")
    needs_coordinates = changes_csv or any(
        candidate.match for candidate, _, _ in validated
    )
    raw_table = (
        CsvTable.read(raw_path) if needs_coordinates and raw_path.exists() else None
    )
    page_rows = raw_table.page(page_id) if raw_table else []
    characters = [(row["Char ID"], row["Unicode"], row_box(row)) for row in page_rows]
    existing = [PageRecord(row.copy(), row["Char ID"]) for row in page_rows]
    existing_by_id = {record.old_char_id: record for record in existing}
    replacements: dict[str, Box] = {}
    additions: list[PageRecord] = []
    logs: list[dict[str, object]] = []
    stamp = datetime.now(timezone.utc)
    for candidate, action, code in validated:
        match = candidate.match
        target = resolve_match(characters, match) if match else None
        if action == "replace":
            if target is None or target in replacements:
                raise HTTPException(
                    409,
                    f"差し替え対象の既存文字を一意に特定できません: {candidate.candidate_id}",
                )
            replacements[target] = candidate.box
        if code is not None:
            additions.append(
                PageRecord(
                    {
                        "Unicode": code,
                        "Image": page_id,
                        **dict(zip(BOX_COLUMNS, map(str, candidate.box))),
                    }
                )
            )
        logs.append(
            {
                "timestamp": stamp.isoformat(),
                "book": book_id,
                "page": page_id,
                "box": candidate.box,
                "status": candidate.status,
                "decision": action,
                "unicode": code,
                "target_char_id": target or (match.char_id if match else None),
            }
        )

    updates: list[CsvUpdate] = []
    char_id_map: dict[str, str] = {}
    if changes_csv and raw_table is not None:
        derivatives: list[CsvUpdate] = []
        for kind, directory in (
            ("output", dirs.output),
            ("output_seg", dirs.output_seg),
        ):
            path = directory / book_id / "column_annotation.csv"
            if not path.exists():
                continue
            table = CsvTable.read(path)
            saved_page = table.page(page_id)
            if not saved_page:
                continue
            derivatives.append(CsvUpdate(kind, path, table))
            for char_id, saved in match_annotations(page_rows, saved_page).items():
                existing_by_id[char_id].annotations[kind] = saved
        for target, box in replacements.items():
            existing_by_id[target].coordinates.update(zip(BOX_COLUMNS, map(str, box)))

        records = existing
        if additions:
            fallback_block = common_block(
                page_rows, common_block(raw_table.rows, "B0001")
            )
            records = order_with_additions(existing, additions, fallback_block)
            for index, record in enumerate(records, 1):
                record.coordinates["Char ID"] = f"C{index:04d}"
        char_id_map = {
            record.old_char_id: record.coordinates["Char ID"]
            for record in records
            if record.old_char_id is not None
        }
        coordinates = raw_table.with_page(
            page_id, [record.coordinates for record in records]
        )
        updates.append(CsvUpdate("raw", raw_path, coordinates))
        if dirs.raw_csv.is_dir():
            updates.append(
                CsvUpdate("raw_csv", dirs.raw_csv / raw_path.name, coordinates)
            )
        for derivative in derivatives:
            rows = [
                {**record.annotations.get(derivative.kind, {}), **record.coordinates}
                for record in records
            ]
            updates.append(
                CsvUpdate(
                    derivative.kind,
                    derivative.path,
                    derivative.table.with_page(page_id, rows),
                )
            )
    backups = persist_updates(updates, logs, stamp, book_id, page_id)
    return {
        "success": True,
        "saved_count": len(logs),
        "added_count": len(additions),
        "replaced_count": len(replacements),
        "rejected_count": len(logs) - len(additions) - len(replacements),
        "remaining_undecided_count": len(candidates.keys() - decided) - len(logs),
        "updated_files": [str(update.path) for update in updates],
        "backup_files": backups,
        "char_id_map": char_id_map,
    }
