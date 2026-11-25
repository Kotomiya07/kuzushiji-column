このプロジェクト（kuzushiji-column）に、以下の仕様で「KCR取り込み確認モード」を実装してください。返答と作業報告は日本語で。既存テスト全件パスが完了条件です。

# 背景
Multi-Stage-KCR という外部プロジェクトが CODH の不足ラベルを補った検出ボックス（座標のみ、文字ラベルなし）を公開した。その差分548件を抽出済みで、既存データと照合した候補ファイルが kcr_import/candidates.json にある。これをユーザーが1件ずつ目視確認して安全に取り込むための画面を追加する。

# 入力データ: kcr_import/candidates.json
候補548件・250ページ。各要素:
- book, page, split（train/valid/test）, box: [x, y, w, h]（ピクセル座標）
- status: 候補の分類
  - "new"（534件）: 既存文字と重ならない純粋な追加候補
  - "conflict_user"（9件）: ユーザー本人が追加した既存箱と IoU 0.79〜0.88 で重なる。match に既存文字（unicode=U+E00A、char_id、box、user_added=true）が入る
  - "near"（5件）: 既存文字と IoU 0.3〜0.5 で部分的に重なる。match に既存文字が入る
- match_iou, in_corrected_list

ページ画像と既存文字の読み込みは既存の仕組み（raw/{book}/images、raw_csv、output）を使うこと。

# 画面と操作
- 候補のあるページだけを巡回できる専用モードを追加する。ページ一覧には候補件数と未判定件数を出す。
- ページ画像上に、既存文字の箱（通常表示）と候補の箱を status 別の色で重ねて表示する。conflict_user と near は、対になる既存箱と候補箱の両方を強調する。
- 候補ごとに判定を選べること:
  - new: 「追加する」「却下」。追加する場合は Unicode 入力が必須（"U+XXXX" 形式でも文字そのものでも可。未入力では保存できないようにする）
  - conflict_user: 「自分の箱を残す（候補を却下）」「KCRの箱に差し替える（座標だけをKCRの箱に替え、Unicode と Char ID は既存文字のものを維持）」「両方残す（KCRの箱を新規文字として追加。Unicode 入力必須）」。既定の選択は「自分の箱を残す」
  - near: 「追加する」「却下」。追加時は Unicode 必須
- 判定は候補ごとに保持し、ページ単位で保存できる。未判定の候補が残っていても保存可能とし、判定済み分だけを反映する。
- 保存済みの候補は、アプリを再起動しても再表示されないこと（後述の decisions.jsonl から判定済みを除外する）。

# 保存仕様
- 保存先は既存アプリのデータフローに従う。実装前に main.py の読み書き経路（raw_csv/{book}_coordinate.csv、raw/{book}/{book}_coordinate.csv、output/{book}/column_annotation.csv の関係）を確認し、それに合わせること。判断に迷う場合は次を正とする: 文字の追加と座標変更は raw_csv と raw の両方の coordinate.csv に同じ内容を書き、output が存在する冊では output の該当ページにも行を追加する（Column ID などは空欄とし、列の付け直し対象であることが分かるようにする）。
- 挿入位置と Char ID の振り直し: 追加文字は、output がある場合は既存の列割り当てから、ない場合は X 座標の列推定から所属列を推定し、列内では Y 昇順で挿入位置を決める。ページ全体の Char ID を読み順（列の順→列内 Y 順）に振り直し、既存文字の Column ID / Wari ID / Wari Part は、旧 Char ID から新 Char ID への対応表で付け替えること（ID の文字列ではなく文字の同一性で追う）。
- バックアップ: CSV を書き換える前に、kcr_import/backups/ へタイムスタンプ付きでコピーする。
- 判定ログ: kcr_import/decisions.jsonl に1判定1行で追記する（日時、book、page、box、status、decision、追加時の unicode、conflict 時の対象 char_id）。

# 制約
- 既存機能を壊さないこと。既存テスト全件パスが条件。
- 新規テストを追加すること。対象: 候補の読み込みと status 分類の表示、conflict_user の「差し替え」保存で座標だけが変わり Unicode と文字数が維持されること、Char ID 振り直しで Column/Wari の対応が保たれること、保存時にバックアップができること、Unicode 未入力の追加は保存できないこと。
- テストで実データの CSV を書き換えないこと（一時ディレクトリで完結させる）。
- 完了条件: `uv run pytest` 全件パス。可能ならアプリを起動し、候補ページの画面が HTTP 200 で返ることも確認する。
- 作業の最後に、変更ファイルの一覧とテスト結果を簡潔に報告すること。

# 追記（再実行時の指示）
- 前回は pytest 実行の承認待ちで止まった。今回は .claude/settings.json にテスト実行などを許可済みなので、承認を待たずに最後まで進めること。方針確認も不要で、前回提示した方針（kcr_import.py への切り出し、raw_csv と raw の両方へ保存、output がある冊では output にも反映）でそのまま実装すること。
- raw/ は ../kuzushiji-dataset-refine/raw へのシンボリックリンクで、書き込み先として許可済み（--add-dir で追加してある）。実データへの書き込みはアプリの通常動作として扱ってよいが、テストでは実データを書き換えないこと。
