# KCR取り込み確認モード 実装計画 (`PLAN.md`)

`kcr_import/BRIEF.md` および既存コードベース（`main.py`, `templates/index.html`, `static/app.js`, `tests/`, `raw_csv/`, `kcr_import/candidates.json`）の調査結果に基づく実装計画です。

---

## 1. 既存 `main.py` のデータ読み書き経路の確認結果（`raw_csv` / `raw` / `output` の関係）

### 1.1 ディレクトリ別の読み書き状況（現状）

| パス | 定数 (`main.py`) | 既存 `main.py` の読み込み | 既存 `main.py` の書き込み | 備考 |
| :--- | :--- | :--- | :--- | :--- |
| `raw/{book}/{book}_coordinate.csv` | `RAW_DIR = Path("raw")` | あり（唯一の座標CSV入力元: `get_page_ids`, `load_coordinate_csv`, `get_completed_pages`, `get_page_data`, `get_page_suggestions`, `generate_column_images`, `save_annotations`） | **なし（読み取り専用）** | 実プロジェクトでは `../kuzushiji-dataset-refine/raw` へのシンボリックリンク。8列: `Unicode,Image,X,Y,Block ID,Char ID,Width,Height` |
| `raw/{book}/images/{page}.jpg` | `RAW_DIR` | あり（`get_image_path`, `get_page_data`, `get_page_image`, `generate_column_images`） | なし | 表示時は `.cache/page_images/` に WebP キャッシュ生成 |
| `raw_csv/{book}_coordinate.csv` | **未定義** | **なし**（`main.py` 内に参照なし） | **なし** | リポジトリ直下に44冊分を保持。候補11冊すべて存在（同じ8列） |
| `output/{book}/column_annotation.csv` | `OUTPUT_DIR = Path("output")` | あり（`load_aligned_existing_annotations`, `load_saved_column_boxes`, `get_completed_pages`, `load_existing_annotations`, `load_page_index`, `get_books`, `get_pages`, `save_segments`） | あり（`save_annotations` で該当ページ行を置換/追記） | 上記8列 + `Column ID` + `Column X, Column Y, Column Width, Column Height` + `Wari ID, Wari Part` |
| `output/{book}/column_annotation.index` | `INDEX_FILENAME` | あり（`load_page_index`, `get_books`, `get_pages`） | あり（新規ページ保存時に追記） | 保存済み `Image` 名の改行区切り一覧。`output` CSV の `Image` 列から再生成されることがある |
| `output/{book}/columns/{page}_col*.jpg` | `OUTPUT_DIR` | なし | あり（`generate_column_images`） | 列保存時にバックグラウンド生成 |
| `output_seg/{book}/column_annotation.csv` | `OUTPUT_SEG_DIR = Path("output_seg")` | あり（`load_aligned_existing_annotations`, `load_existing_segment_*`, `build_segment_override_for_page`） | あり（`_update_output_seg_csv`） | `output` の列に `Segment ID` を追加した派生CSV |

> **環境上の注記**: 本作業フォルダは実プロジェクトのコピー構成であり、`~/Documents/project/kuzushiji-column/{raw,output}` への直接参照は macOS TCC 制限により実行できないため、実ファイルの存在一覧は未参照です。ただし `main.py` の全入出力ロジック・既存テスト全件・本ワークスペース内の `raw_csv/`（全44冊）・`kcr_import/candidates.json`（548件・11冊・250ページ）からデータ形式と読み書き経路を確定しています。

### 1.2 実データ（`candidates.json` × `raw_csv/`）の照合で判明した重要事項

1. **`raw` と `raw_csv` の間で事前ズレがあるケースが存在する**
   - `conflict_user` 全9件のうち、`200022050` の8件は `raw_csv/200022050_coordinate.csv` に `U+E00A` で完全一致して存在する。
   - 一方、`200015843_00094_1` の `C0024`（`match`: `U+E00A`, `[1738, 516, 67, 133]`, `user_added=true`）は `raw_csv/200015843_coordinate.csv` には存在せず、同ファイルの `C0024` は `U+304F`（`[1733, 652, 42, 119]`）となっている。
   - **設計への反映**:
     - 読み込みの一次ソースは既存 `main.py` と同じく `raw/{book}/{book}_coordinate.csv` とする。
     - `conflict_user` の差し替え対象行を特定する際は、`char_id` の一致だけでなく `Unicode`・座標近傍でも検証し、別文字を誤上書きしないガードを入れる（詳細は 4.1 節）。
2. **既存座標CSVに行が0件のページが候補に含まれる**
   - `100241706_00002_1`（`new` 26件）は `raw_csv/100241706_coordinate.csv` に1行も存在しない（最初のページは `100241706_00004_2`）。
   - **設計への反映**:
     - 既存文字0件のページでも画像（`raw/{book}/images/{page}.jpg`）があれば画面表示・新規追加保存を可能にする（404 にしない）。
     - 追加文字の `Block ID` はページ内既存行の最頻値 → 冊内最頻値 → 既定値 `"B0001"` の順で補完する（対象11冊の既存行はほぼすべて `"B0001"`）。
3. **既存 `output` / `get_completed_pages` / `align_saved_annotations_to_current_page` との整合性**
   - `get_completed_pages(book_id)` は、`output` と `raw` のページ行数が一致し、全行に `Column ID` があり、全列に `Column X..Height` が揃っているページを「完了（✅）」とする。KCR 追加行を `Column ID` 等空欄で `output` に挿入すると、そのページは**自動的に未完了（列付け直し対象）に戻る**。
   - `load_page_index(book_id)` は `output/{book}/column_annotation.csv` 内に現れる全 `Image` を「アノテーション済み」とみなすため、**`output/{book}/column_annotation.csv` が存在しても、まだアノテーションされていないページ（該当ページの行が0件）には `output` へ行を追加しない**。既存ページ行がある場合にのみ `output`（および該当ページ行がある `output_seg`）を更新する。
   - `align_saved_annotations_to_current_page` 末尾の `_fill_unassigned_chars_inside_neighbor_column` は、前後同列かつ列 bbox に完全内包される未割当文字を表示時に補完する既存仕様がある（CSV 上の `Column ID` は空欄のままのため、`get_completed_pages` の未完了判定には影響しない）。
4. **ホットリロード除外 (`APP_RELOAD_EXCLUDES`)**
   - `main.py` の `APP_RELOAD_EXCLUDES` に `raw_csv/*`, `kcr_import/backups/*`, `kcr_import/decisions.jsonl` が含まれていないため、保存時に uvicorn `--reload` が再起動しないよう除外対象へ追加する（既存テスト `tests/test_server_startup.py` の要件 `"output/*" in main.APP_RELOAD_EXCLUDES` は維持）。
5. **既存 UI テストの制約**
   - `tests/test_column_boxes_ui.py` と `tests/test_wari_ui.py` が `templates/index.html` 内の `/static/app.js?v=24` を厳密に検証しているため、既存 `index.html` の `app.js` バージョンや既存 JS ロジックは変更せず、KCR 取り込み確認モードは専用ページ（`/kcr-import`）と独立 JS/CSS として実装する。

### 1.3 KCR 取り込み時の読み書き経路まとめ

- **読み込み**:
  - 候補: `kcr_import/candidates.json`
  - 判定履歴: `kcr_import/decisions.jsonl`
  - 既存文字座標: `raw/{book}/{book}_coordinate.csv`（既存 `main.load_coordinate_csv` と同経路）
  - ページ画像: `raw/{book}/images/{page}.jpg`（既存 `GET /api/books/{book}/pages/{page}/image` を再利用）
  - 既存列・割注: `output/{book}/column_annotation.csv`（存在時）、`output_seg/{book}/column_annotation.csv`（存在時）
- **書き込み**（CSV 変更を伴う判定が1件以上ある場合、すべてバックアップ作成後に `.tmp` 経由で原子的置換）:
  1. `raw/{book}/{book}_coordinate.csv`
  2. `raw_csv/{book}_coordinate.csv`（`raw` と同一内容を書き込む）
  3. `output/{book}/column_annotation.csv`（ファイルが存在し、かつ該当ページの既存行が存在する場合のみ更新）
  4. `output_seg/{book}/column_annotation.csv`（ファイルが存在し、かつ該当ページの既存行が存在する場合のみ更新）
- **ログ・バックアップ**:
  - バックアップ: `kcr_import/backups/{YYYYMMDD_HHMMSS_ffffff}_{book}_{page}/`
  - 判定ログ: `kcr_import/decisions.jsonl`（全判定を1判定1行で追記。却下/`keep_existing` のみの場合は CSV を触らずログ追記のみ）

---

## 2. 追加するモジュールと API の一覧

### 2.1 ファイル構成（追加・変更予定）

| ファイル | 種別 | 役割 |
| :--- | :--- | :--- |
| `kcr_import.py` | **新規** | 候補読み込み・判定済み除外・Unicode 正規化検証・所属列推定・Char ID 振り直し・Column/Wari/Segment 付け替え・バックアップ・CSV 原子的更新・`decisions.jsonl` 追記 |
| `main.py` | **最小変更** | `RAW_CSV_DIR` 定数追加、`APP_RELOAD_EXCLUDES` 更新、`/kcr-import` 画面ルートと `/api/kcr-import/*` API の登録（処理は `kcr_import.py` へ委譲） |
| `templates/index.html` | **最小変更** | トップバーの `.topbar__actions` に `/kcr-import` への遷移リンクボタンを追加（既存 `app.js?v=24` / `style.css?v=11` / 既存 DOM ID は変更しない） |
| `templates/kcr_import.html` | **新規** | KCR 取り込み確認モード専用テンプレート |
| `static/kcr_import.js` | **新規** | 確認画面のフロントエンドロジック（ページ巡回・Canvas 描画・候補選択・拡大プレビュー・判定管理・Unicode 検証・保存） |
| `static/kcr_import.css` | **新規** | 確認画面のスタイル（既存 `style.css` の墨と和紙デザイントークンを継承） |
| `tests/test_kcr_import.py` | **新規** | `kcr_import.py` および `/api/kcr-import/*`, `/kcr-import` のユニット・統合テスト（すべて `tmp_path` で完結） |

### 2.2 `kcr_import.py` の主要コンポーネント

テストでの差し替え容易性を重視し、ディレクトリパスはデフォルト値または設定オブジェクト経由で受け取れる設計にする（`main.RAW_DIR` 等への `monkeypatch` も正しく反映されるよう、`main.py` 側のエンドポイントから `main.RAW_DIR`, `main.RAW_CSV_DIR`, `main.OUTPUT_DIR`, `main.OUTPUT_SEG_DIR` を動的に渡す）。

- **定数**:
  - `KCR_DIR = Path("kcr_import")`
  - `CANDIDATES_PATH = KCR_DIR / "candidates.json"`
  - `DECISIONS_PATH = KCR_DIR / "decisions.jsonl"`
  - `BACKUPS_DIR = KCR_DIR / "backups"`
  - `COORDINATE_COLUMNS = ["Unicode", "Image", "X", "Y", "Block ID", "Char ID", "Width", "Height"]`
- **Pydantic モデル**:
  - `KcrDecisionInput`:
    - `candidate_id: str`（一意キー: `f"{book}:{page}:{x},{y},{w},{h}"` — 全548件で `(book, page, box)` 重複ゼロを確認済み）
    - `decision: Literal["add", "reject", "keep_existing", "replace", "keep_both"]`
    - `unicode: Optional[str] = None`
  - `KcrSaveRequest`:
    - `decisions: list[KcrDecisionInput]`
- **主要関数**:
  - `make_candidate_id(book: str, page: str, box: list[int]) -> str`
  - `normalize_unicode_input(raw_value: Optional[str]) -> str`
    - `"U+XXXX"` / `"u+xxxx"`（4〜6桁16進、上限 `0x10FFFF`）→ `"U+XXXX"`（大文字・最低4桁ゼロ埋め）
    - 単一の Unicode 文字（例: `"あ"` → `"U+3042"`, `"𠀋"` → `"U+2000B"`）
    - 空文字・空白・複数文字・不正形式は `ValueError`
  - `load_candidates(candidates_path: Path) -> list[dict]`
  - `load_decided_keys(decisions_path: Path) -> set[str]`
  - `list_candidate_pages(candidates_path: Path, decisions_path: Path, book_id: Optional[str] = None, only_undecided: bool = False) -> dict`
  - `get_candidate_page_detail(book_id: str, page_id: str, ...) -> dict`
  - `resolve_conflict_target_row(page_rows: list[dict], match: dict) -> tuple[Optional[int], Optional[str]]`
  - `estimate_columns_from_x(rows: list[dict]) -> list[list[int]]`
  - `insert_and_renumber_page_rows(...) -> tuple[list[dict], dict[str, str]]`
  - `backup_files(targets: list[tuple[str, Path]], backups_dir: Path, book_id: str, page_id: str) -> list[Path]`
  - `apply_kcr_decisions(book_id: str, page_id: str, decisions: list[KcrDecisionInput], ...) -> dict`
  - `append_decision_logs(decisions_path: Path, entries: list[dict]) -> None`

### 2.3 追加する画面ルート・API 一覧

| Method & Path | 概要 | リクエスト / クエリ | レスポンス概要 |
| :--- | :--- | :--- | :--- |
| `GET /kcr-import` | KCR 取り込み確認画面 HTML を返す | — | `text/html` (HTTP 200) |
| `GET /api/kcr-import/pages` | 候補のあるページ一覧と全体進捗を返す | `book_id?: str`, `only_undecided?: bool = false` | `{ summary: { total_candidates, decided_count, undecided_count, total_pages, undecided_pages }, books: [...], pages: [{ book_id, page_id, total_count, undecided_count, decided_count, status_counts: { new, conflict_user, near } }] }` |
| `GET /api/kcr-import/books/{book_id}/pages/{page_id}` | 対象ページの既存文字・未判定候補・画像メタを返す | — | `{ book_id, page_id, image_width, image_height, image_version, has_output_page, total_count, decided_count, undecided_count, characters: [...], candidates: [{ candidate_id, book, page, split, box, status, match_iou, match, in_corrected_list, default_decision, resolved_match_char_id, match_warning }] }` |
| `POST /api/kcr-import/books/{book_id}/pages/{page_id}/save` | ページ内の判定済み候補を保存（CSV 更新・バックアップ・ログ追記） | `{ decisions: [{ candidate_id, decision, unicode? }] }` | `{ success: true, saved_count, added_count, replaced_count, rejected_count, remaining_undecided_count, updated_files: [...], backup_files: [...], char_id_map: {...} }` |

※ページ画像の取得は既存 `GET /api/books/{book_id}/pages/{page_id}/image?variant=display` をそのまま利用する。

---

## 3. 取り込み確認画面の構成

既存の「墨と和紙テーマ」（`static/style.css` の CSS 変数）を継承した **3カラム構成** で実装する。通常画面 (`/`) のヘッダーから「KCR取込」リンクで遷移でき、確認画面ヘッダーから通常画面へ戻れる。

### 3.1 レイアウト構成

1. **トップバー（ヘッダー）**
   - タイトル「KCR 取り込み確認」＋「通常モードへ戻る」リンク（`/`）
   - 全体進捗チップ（例: `未判定 548 / 548`・`残ページ 250 / 250`）
   - ページ送りボタン: 「前のページ」「次のページ」「次の未判定ページ」
   - 「保存」ボタン（未保存の判定件数を表示。Unicode 未入力などエラーがある場合は無効化＋エラー理由を明示）

2. **左サイドバー（候補ページ一覧）**
   - フィルタUI:
     - 書籍セレクト（全11冊 / 書籍別、各書籍の未判定件数付き）
     - 「未判定のみ表示」チェックボックス
     - 「conflict / near を含むページのみ」絞り込みチェックボックス
   - 候補のあるページだけ（最大250ページ）を一覧表示:
     - `book_id / page_id`
     - **候補件数と未判定件数**（例: `未 3 / 全 5`）
     - `conflict_user`・`near` を含むページには色付きドット/件数バッジを表示
     - 未判定 0 件のページは完了スタイル（✓）で表示

3. **中央ステージ（ページ画像キャンバス）**
   - ページ画像上に以下を重ねて描画（ウィンドウリサイズ対応、クリックで候補選択）:
     - **既存文字の箱（通常表示）**: 控えめな細枠（グレー系 1px）で全既存文字を描画
     - **候補の箱（`status` 別カラー）**:
       - `new`: 緑系（例: `#15803d` 枠 + 半透明塗り）
       - `conflict_user`: 赤系（例: `#c53030` 枠 + 半透明塗り）
       - `near`: 橙系（例: `#d97706` 枠 + 半透明塗り）
     - **`conflict_user` / `near` のペア強調**:
       - 対になる既存文字の箱（`resolved_match_char_id` または `match.box`）をシアン/青系の太破線枠で強調し、候補箱と両方を目立たせる。選択中はさらに両者を結ぶハイライトとラベル（`既存: C0405 (U+E00A)` / `候補: KCR`）を表示
     - **選択中の候補**: 二重枠＋番号バッジ（`#1`, `#2`, ...）で強調
     - **保存前の判定状態反映**: 却下/`keep_existing` 選択時は破線・半透明、追加/`replace`/`keep_both` 選択時は実線＋入力文字（例: `あ`）を箱の横に小さく表示

4. **右サイドバー（候補判定パネル）**
   - 該当ページの未判定候補カード一覧（番号 `#1`, `#2`, ... でキャンバスと双方向同期）
   - 各候補カードの内容:
     - 番号・`status` バッジ・`split`・座標 `[x, y, w, h]`・`in_corrected_list === false` の注意表示
     - **切り出し拡大プレビュー（ミニ Canvas）**: 元画像から候補箱（および `conflict_user`/`near` の対の既存箱）周辺に余白を付けて拡大表示し、くずし字を目視判読しやすくする
     - 対の既存文字情報（`conflict_user` / `near` 時）: `Char ID`、`Unicode`（文字変換プレビュー付き）、`IoU`、および `match_warning` がある場合の警告表示
     - **判定選択肢**:
       - `new` / `near`: 「未判定」 / 「追加する (`add`)」 / 「却下 (`reject`)」
       - `conflict_user`: **「自分の箱を残す (`keep_existing`)」（初期既定値）** / 「KCRの箱に差し替える (`replace`)」 / 「両方残す (`keep_both`)」 / 「未判定にする」
     - **Unicode 入力欄**（`add` / `keep_both` 選択時に表示・必須）:
       - `"U+XXXX"` 形式でも文字そのもの（1文字）でも入力可
       - リアルタイムで変換結果（例: `あ → U+3042`）を表示。未入力または不正形式なら赤枠＋エラー文言を表示し、保存ボタンをブロック
   - パネル下部:
     - ページ内判定サマリ（追加 / 差し替え / 却下 / 未判定 件数）
     - 未判定が残っていても判定済みが1件以上あれば保存可能（判定済み分だけを送信）
     - 未保存の変更がある状態でページ移動しようとした場合は確認ダイアログを表示

---

## 4. 保存処理と Char ID 振り直し・Column/Wari 付け替えの手順

### 4.1 事前バリデーション（1件でも不正ならファイル変更なし）

1. `candidates.json` と `decisions.jsonl` から対象ページの**未判定候補**をロード。
2. リクエストの `decisions` が空でないこと、重複 `candidate_id` がないこと、全 `candidate_id` が当該ページの未判定候補であることを確認（既に判定済みなら HTTP 409、未知の候補なら HTTP 400）。
3. `status` ごとに許可された `decision` を検証:
   - `new` / `near`: `{"add", "reject"}`
   - `conflict_user`: `{"keep_existing", "replace", "keep_both"}`
4. `decision in {"add", "keep_both"}` の全項目について `normalize_unicode_input(item.unicode)` を実行。未入力・空白・複数文字・不正な `U+` 形式なら即 HTTP 400 を返す。
5. `decision == "replace"` の全項目について、`raw/{book}/{book}_coordinate.csv` の該当ページ内で差し替え対象既存行を特定:
   - 第1優先: `Char ID == match.char_id` の行が `Unicode == match.unicode` かつ `match.box` との IoU >= 0.5 を満たす場合に確定。
   - 第2優先（`Char ID` ズレ時のフォールバック）: 該当ページ内で `Unicode == match.unicode` かつ `match.box` との IoU >= 0.7 の行が一意に特定できる場合に確定。
   - 特定できない場合（または複数候補が競合する場合）: 誤上書き防止のため HTTP 409 を返して中断する。

### 4.2 バックアップの作成

- CSV 変更を伴う判定（`add`, `replace`, `keep_both`）が **0件**（`reject` / `keep_existing` のみ）の場合:
  - CSV 書き換え・バックアップはスキップし、4.5 節の `decisions.jsonl` 追記のみ行う。
- CSV 変更を伴う判定が **1件以上** ある場合:
  - 書き換え対象の実在ファイルを特定:
    1. `raw/{book}/{book}_coordinate.csv`（必須、無ければ HTTP 404）
    2. `raw_csv/{book}_coordinate.csv`（存在すればバックアップ）
    3. `output/{book}/column_annotation.csv`（存在し、かつ該当ページの既存行が存在する場合のみ更新・バックアップ）
    4. `output_seg/{book}/column_annotation.csv`（存在し、かつ該当ページの既存行が存在する場合のみ更新・バックアップ）
  - `kcr_import/backups/{YYYYMMDD_HHMMSS_ffffff}_{book}_{page}/` を作成し、上記すべての既存ファイルを `shutil.copy2` でコピーする（ファイル名に `raw_`, `raw_csv_`, `output_`, `output_seg_` 接頭辞を付けて衝突を防ぐ）。

### 4.3 ページ行モデルの構築と所属列推定・Char ID 振り直し・Column/Wari 付け替え

既存文字の属性（`Column ID`, `Column X..Height`, `Wari ID`, `Wari Part`, `Segment ID`）を **Char ID 文字列ではなく文字レコードの同一性（一意な内部レコードオブジェクト / `origin_token`）** で追跡する。

1. **既存文字レコードの読み込みとアノテーション紐付け**:
   - `raw/{book}/{book}_coordinate.csv` から該当ページ行を `Char ID` 昇順で読み込み、各行に `origin_token` と `old_char_id` を付与。
   - `output/{book}/column_annotation.csv` に該当ページの既存行がある場合は、既存 `main.align_saved_annotations_to_current_page` と同様の文字同一性照合（fingerprint 一致時は `Char ID` 直接対応、不一致時は Unicode・Block ID・座標近傍照合）を用いて、各既存文字レコードへ元の `Column ID`・`Column X..Height`・`Wari ID`・`Wari Part` を紐付ける。
   - `output_seg/{book}/column_annotation.csv` に該当ページの既存行がある場合も同様に `Segment ID` を紐付ける。

2. **`replace`（差し替え）の適用**:
   - 特定済みの既存文字レコードの `X, Y, Width, Height` だけを KCR 候補の `[x, y, w, h]` に更新する。
   - `Unicode`, `Block ID`, `origin_token`, `old_char_id` および紐付けた `Column ID` / `Column X..Height` / `Wari ID` / `Wari Part` / `Segment ID` はすべてそのまま維持する。
   - **重要**: 当該ページの保存内容が **`replace` のみ（`add` / `keep_both` が0件）** の場合、文字数・読み順は変えず、既存文字の `Char ID` をそのまま維持する（座標の微小差で意図せず `Char ID` が入れ替わるのを防ぐ）。

3. **`add` / `keep_both`（新規文字）のレコード作成**:
   - `Unicode` = 正規化済み `"U+XXXX"`、`Image` = `page_id`、`X, Y, Width, Height` = KCR 候補箱、`Block ID` = 所属列/ページ/冊の最頻出 `Block ID`（既定 `"B0001"`）。
   - `is_new = True` とし、`Column ID` / `Column X..Height` / `Wari ID` / `Wari Part` / `Segment ID` はすべて空欄（`""`）とする。

4. **所属列の推定と挿入位置の決定（新規追加が1件以上ある場合）**:
   - **パターン A: `output` に該当ページの `Column ID` 割り当てが存在する場合**
     1. 既存文字を既存 `Column ID` ごとにグループ化し、既存の列順（`COL0001`, `COL0002`, ... の順）と列内の既存相対順（`old_char_id` 順）を保持する。
        - もし `Column ID` が空の既存文字があれば、X 中心距離が最小の既存列（しきい値外なら X 降順の新規仮列）へ仮所属させる。
     2. 各既存列の水平範囲 `[min_x, max_x]`、代表 X 中心（列内文字の中心 X の中央値）、ページ平均文字幅 `avg_w` を算出する。
     3. 各新規文字（中心 `cx = x + w/2`, `cy = y + h/2`）の所属列を推定:
        - 既存列と水平方向に重なる、または `|cx - col_center_x| <= max(avg_w * 0.8, col_width * 0.6)` を満たす列のうち、`cx` と `col_center_x` の距離が最小の列を所属列とする。
        - どの既存列ともしきい値を超えて離れる場合は新規の仮列を作成し、全列の代表 X 中心の降順（右→左）で適切な列順位置に挿入する。
     4. **列内の挿入位置（Y 昇順、かつ既存割注の順序保護）**:
        - 通常の既存列（割注なし）では列内既存文字も Y 昇順であるため、新規文字を中心 Y（同値時は上端 Y）の昇順位置に挿入する。
        - 割注（`Wari ID` あり）を含む既存列では、割注内が右行（上→下）→左行（上→下）と Y が戻るため、列全体を単純 Y ソートせず、**既存文字同士の相対順（`old_char_id` 順）を固定したまま**、新規文字より中心 Y が大きい最初の既存文字（割注ブロックはそのブロックの代表 Y 範囲で比較）の直前へ新規文字を挿入する。これにより、Y 昇順の自然な挿入位置と既存割注の right→left 構造保持を両立する。
        - 新規仮列に複数の新規文字が入った場合は、その仮列内を中心 Y 昇順（同値時は上端 Y 昇順）で並べる。
   - **パターン B: `output` が存在しない（または該当ページに `Column ID` が無い）場合**
     1. 既存文字がある場合は、既存 `app.js` の `autoEstimateColumns` と同じ基準（全文字の中心 X 降順ソート、しきい値 `avg_w * 0.8` による X 座標クラスタリング）で既存文字の列構造を推定し、各新規文字を最も近い列（しきい値外なら新規仮列）に割り当て、列内は中心 Y 昇順で並べる。
     2. 既存文字が0件のページ（例: `100241706_00002_1`）の場合は、新規文字群に対して X 座標クラスタリング（右→左、しきい値 `avg_w * 0.8`）を行い、各列内を中心 Y 昇順で並べる。

5. **ページ全体の Char ID 振り直しと旧→新対応表の構築**:
   - 「列順 → 列内順」に平坦化した全レコードに対し、先頭から `C0001`, `C0002`, ..., `C{N:04d}` を順次付与する。
   - 既存文字レコード（`is_new == False`）について `char_id_map[old_char_id] = new_char_id` を構築する。
   - 既存文字の `Column ID` / `Column X..Height` / `Wari ID` / `Wari Part` / `Segment ID` はレコードの同一性（`origin_token`）により新しい `Char ID` の行へそのまま引き継がれ、新規文字行は空欄のままとなる。

### 4.4 CSV の原子的書き換え

全対象ファイルについて、同一ディレクトリの一時ファイル（`*.tmp`）へ書き出した後に `Path.replace()` で置換する。

1. **`raw/{book}/{book}_coordinate.csv` と `raw_csv/{book}_coordinate.csv`**:
   - 元のヘッダー順（`Unicode,Image,X,Y,Block ID,Char ID,Width,Height`）を維持。
   - 元 CSV の該当ページ（`Image == page_id`）の行群を、更新・振り直し後のページ行群に差し替える（他ページの順序・内容は不変。元 CSV に該当ページが無かった場合は末尾へ追加）。
   - `raw` と `raw_csv` の両方に完全に同じ内容を書き込む（`raw_csv` の親ディレクトリが存在すれば必ず同期書き込み）。
2. **`output/{book}/column_annotation.csv`**（存在かつ該当ページの既存行がある場合のみ）:
   - 既存ヘッダー列順（`fieldnames`）を維持し、該当ページ行を差し替える。
   - 既存文字行には新 `Char ID`・（差し替え時は新座標）・元の `Column ID` / `Column X..Height` / `Wari ID` / `Wari Part` を書き出し、新規文字行は `Column ID` 等を空文字 `""` で書き出す（`"nan"` を出さない）。
3. **`output_seg/{book}/column_annotation.csv`**（存在かつ該当ページの既存行がある場合のみ）:
   - 同様に既存ヘッダー列順を維持し、既存文字行には元の `Segment ID` を含め引き継ぎ、新規文字行は空文字 `""` で書き出す。

### 4.5 判定ログの追記 (`kcr_import/decisions.jsonl`)

CSV 更新成功後（または却下/`keep_existing` のみの検証通過後）に、判定ごとに1行の JSON（UTF-8, `ensure_ascii=False`）を `kcr_import/decisions.jsonl` へ追記する。

```json
{
  "timestamp": "2026-11-23T05:00:00+00:00",
  "book": "200022050",
  "page": "200022050_00013_2",
  "box": [325, 737, 83, 128],
  "status": "conflict_user",
  "decision": "replace",
  "unicode": null,
  "target_char_id": "C0405"
}
```

- `unicode`: `add` / `keep_both` 時は正規化済み `"U+XXXX"`、それ以外は `null`。
- `target_char_id`: `conflict_user`（および `near` の場合は任意参照）の対象既存文字の旧 `Char ID`、`new` の場合は `null`。

---

## 5. 追加するテストの項目

新規テストファイル `tests/test_kcr_import.py` を作成する。すべてのテストは `tmp_path` と `monkeypatch` で一時ディレクトリ上のダミーデータのみを使用し、**実データ（`raw/`, `raw_csv/`, `output/`, `kcr_import/decisions.jsonl`, `kcr_import/backups/`）を一切書き換えない**。

### 5.1 候補読み込み・status 分類・判定済み除外
1. `candidates.json` から `new` / `conflict_user` / `near` の各分類が正しく読み込まれ、`GET /api/kcr-import/pages` がページ別の候補件数・未判定件数・ステータス内訳を返すこと。
2. `GET /api/kcr-import/books/{book}/pages/{page}` が各候補の `status`・`match`・`default_decision`（`conflict_user` は `"keep_existing"`）と既存文字一覧を返すこと。
3. `decisions.jsonl` に保存済みの候補が、再読み込み（アプリ再起動相当）後に除外されて再表示されないこと。
4. 1ページ内に複数候補がある状態で一部だけ保存したとき、未判定候補が残っていても保存成功し、判定済み分のみ反映・ログ記録され、未判定候補はそのまま残ること。
5. 座標 CSV に既存行が0件のページ（`100241706_00002_1` 相当）でもページ詳細取得・新規文字追加保存ができること。

### 5.2 Unicode 入力バリデーション・正規化
6. `"U+3042"`, `"u+3042"`, `"あ"`, サロゲート外文字（例: `"𠀋"` → `"U+2000B"`）が `"U+XXXX"` 形式へ正規化されること。
7. `new` の `add`、`near` の `add`、`conflict_user` の `keep_both` で Unicode が未入力（`None` / `""` / 空白のみ）または不正形式（2文字以上、`"U+GGGG"` 等）の場合、HTTP 400 で拒否され、CSV も `decisions.jsonl` も一切変更されないこと。

### 5.3 `conflict_user` の保存挙動
8. `conflict_user` の「KCRの箱に差し替える (`replace`)」保存で、`raw` と `raw_csv` の両方（および `output` 存在時は `output`）で対象文字の座標 (`X, Y, Width, Height`) のみが KCR の箱に変わり、**Unicode・Char ID・文字総数が維持されること**。
9. `conflict_user` の「自分の箱を残す (`keep_existing`)」保存で、CSV は変更されず、`decisions.jsonl` にのみ記録されること。
10. `conflict_user` の「両方残す (`keep_both`)」保存で、既存文字を残したまま KCR の箱が新規文字として追加され、文字総数が +1 されること。
11. `conflict_user` の `match.char_id` が現在の `raw` CSV とズレて別文字を指している場合でも、別文字を誤上書きしないこと（座標・Unicode 一致フォールバック、または一致不能時 HTTP 409）。

### 5.4 挿入位置決定・Char ID 振り直し・Column / Wari 付け替え
12. `output` が存在するページで新規文字を追加したとき、既存列割り当てから所属列を推定し、列内 Y 昇順に挿入され、ページ全体の `Char ID` が `C0001..C{N:04d}` に振り直されること。
13. **Char ID 振り直し後、既存文字の `Column ID` / `Column X..Height` / `Wari ID` / `Wari Part`（および `output_seg` 存在時は `Segment ID`）が旧 Char ID から新 Char ID へ文字の同一性で正確に付け替えられ、追加文字の列・割注欄は空欄になること**。
14. 割注（`Wari ID` / `Wari Part`）を含む列に新規文字を挿入しても、既存割注の `right` → `left` 順序と対応が壊れないこと。
15. `output` が存在しないページで新規文字を追加したとき、X 座標による列推定から所属列が決まり、読み順（右→左列、列内 Y 昇順）で `Char ID` が振り直されること。
16. `output` ありページへ新規文字を保存した直後、`main.get_completed_pages(book)` で該当ページが未完了になり、`main.get_page_data(book, page)` で既存文字の `column_id` / `wari_id` / `wari_part` が正しく復元されること。

### 5.5 同期書き込み・バックアップ・ログ・画面 HTTP 200
17. 追加・差し替え保存時、`raw/{book}/{book}_coordinate.csv` と `raw_csv/{book}_coordinate.csv` の内容が完全一致すること。
18. `output` が存在する冊（該当ページあり）では `output/{book}/column_annotation.csv` にも反映され、`output` が無い冊（または未アノテーションページ）では `output` が不要に作成・汚染されないこと。
19. 保存前に `kcr_import/backups/` 配下へタイムスタンプ付きで対象 CSV のバックアップが作成され、書き換え前の内容と一致すること。
20. `kcr_import/decisions.jsonl` に 1 判定 1 行で必須フィールド（`timestamp`, `book`, `page`, `box`, `status`, `decision`, `unicode`, `target_char_id`）が正しく追記されること。
21. `GET /kcr-import` が HTTP 200 を返し、確認画面テンプレートが描画されること。また `GET /` の通常画面および既存テスト全件（`uv run pytest`）が壊れず全件パスすること。

---

## 6. 実装・検証の進め方（実装フェーズ向け）

1. **Red → Green → Refactor (TDD)**:
   - まず `tests/test_kcr_import.py` にバックエンドのコアテスト（読み込み・Unicode 検証・差し替え・Char ID 振り直しと Column/Wari 付け替え・バックアップ・ログ）を作成し Red を確認。
   - `kcr_import.py` と `main.py` の API / ルートを実装して Green にする。
   - `templates/kcr_import.html`, `static/kcr_import.js`, `static/kcr_import.css`, および `templates/index.html`（遷移リンクのみ）を実装。
2. **完了検証**:
   - `uv run pytest` で既存テスト＋新規テスト全件パスを確認。
   - `uv run ruff check .` でリント確認。
   - 開発サーバーを起動し、`GET /kcr-import` および候補ページの API が HTTP 200 で返ることを確認。
