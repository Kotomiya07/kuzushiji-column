# くずし字列分割アノテーションツール

日本古典籍くずし字データセットの列分割アノテーションを行うWebアプリケーションです。各文字のbboxを列単位でグループ化し、列画像とCSVアノテーションを出力します。

## 機能

- ページ画像上で文字のbboxを可視化
- キーボード/タッチ操作による効率的な列分割
- X座標に基づく列の自動推定（Autoモード）
- 書籍ごとの統計モデルによる列/セグメント推論
- 全書籍共通モデル + 書籍別モデルの二段構え推論
- human in the loop での予測修正と継続学習
- 書籍ごとのモデル状態、学習時 metrics、HITL feedback の可視化
- 列単位でのbbox表示と色分け
- 列/セグメント表示の切り替え
- 列アノテーションからセグメント（Segment ID）を自動推定して出力
- Undo/Redo対応
- 列画像の自動切り出し・保存

## 必要要件

- Python 3.12以上
- uv（パッケージマネージャー）

## インストール

```bash
# リポジトリをクローン
git clone <repository-url>
cd kuzushiji-column

# 依存パッケージをインストール
uv sync
```

## データの準備

`raw/` ディレクトリに以下の構造でデータを配置してください：

```
raw/
└── {book_id}/
    ├── {book_id}_coordinate.csv   # 文字座標CSV
    └── images/
        ├── {page_id}.jpg          # ページ画像
        └── ...
```

### 座標CSVの形式

| 列名 | 説明 |
|------|------|
| Image | ページID（画像ファイル名から拡張子を除いたもの） |
| Unicode | 文字のUnicodeコードポイント |
| X | bboxの左上X座標 |
| Y | bboxの左上Y座標 |
| Width | bboxの幅 |
| Height | bboxの高さ |
| Char ID | 文字ID（例: C0001, C0002, ...） |
| Block ID | ブロックID（オプション） |

## 起動方法

```bash
uv run python main.py
```

この起動方法ではホットリロードが有効です。`main.py` を変更すると開発サーバーが自動再起動します。

ブラウザで http://localhost:8100 を開きます。

別コマンドで起動する場合も、同じくホットリロード付きで `8100` 番ポートを使ってください。

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8100
```

## 使い方

### 基本操作

1. 書籍とページを選択（起動時は自動で最初のページを表示）
2. ↓キーで列の最後の文字まで移動
3. 次の列の先頭文字でEnterキー → 直前までが列として確定
4. 最後の列は、最後の文字でEnterキー → 残り全部が確定
5. Sキーで保存 → CSVと列画像を出力

### 表示モード

ツールバーのトグルで「列」表示と「セグメント」表示を切り替えられます。

### キーボードショートカット

| キー | 操作 |
|------|------|
| ↑ / ↓ | 文字選択を移動 |
| ← / → | 前後のページへ移動 |
| Enter | 列を確定（選択した文字から新しい列開始） |
| N | 選択文字を未分割に戻す |
| Shift + N | 全解除（全て未分割に戻す） |
| Z | 元に戻す (Undo) |
| Y | やり直し (Redo) |
| S | 保存 |
| H | ヘルプを表示 |

### タッチ操作（スマホ/タブレット）

- ツールバーのボタンで `列確定` / `未分割へ` を実行できます（キーボードなしで操作可能）
- 画像領域の右端にある `↑/↓` ボタンで選択文字を移動できます（押しっぱなしで連続移動）
- セグメント表示中は `列確定` / `未分割へ` が無効化されます（閲覧モード）
- `列確定` は未分割文字がない場合、`未分割へ` は選択文字が未割当の場合に無効化されます

### 表示の見方

- **青枠**: 未割り当ての文字
- **ピンク塗り**: 選択中の文字
- **色付き背景**: 確定済みの列（列ごとに色分け）
- **赤点線**: Char ID順の重心を結ぶ線

### Autoモード

ツールバーの「Auto」トグルをONにすると、既存のアノテーションがないページを開いた時に、まず学習済みモデルによる推論を試みます。モデルが未学習またはサンプル不足の場合だけ、従来のX座標ベース自動推定にフォールバックします。推定結果を修正してから保存できます。

### Human in the Loop

- 列保存後とセグメント保存後に、現在の書籍のモデルをバックグラウンド再学習します
- 同時に、全書籍共通モデルも再学習します
- 新規ページではモデル推論を初期アノテーションとして読み込みます
- 推論の優先順位は `書籍別モデル -> 全書籍共通モデル -> 従来Auto` です
- 推論結果が誤っていれば既存UIで修正し、その保存結果が次の学習データになります
- 予測も保存も、基準は常に単文字 bbox に紐づく `Column ID` / `Segment ID` です
- 左側パネルで、列/セグメントモデルの利用可否、学習サンプル数、学習時 metrics、直近の HITL feedback を確認できます
- 保存時には、初期予測のうち何件が採用され何件が修正されたかを feedback として `output_models/{book_id}/feedback_history.jsonl` に蓄積します

### 自動未分割化

未分割の文字に挟まれた文字（連続区間）は、自動的に未分割へ戻されます。ページ読み込み時と列確定後に適用されます。

## 出力ファイル

保存時に以下のファイルが生成されます：

```
output/
└── {book_id}/
    ├── column_annotation.csv      # 列アノテーションCSV
    └── columns/
        ├── {page_id}_col0001.jpg  # 列画像
        ├── {page_id}_col0002.jpg
        └── ...

output_seg/
└── {book_id}/
    └── column_annotation.csv      # Segment ID を追加した派生CSV

output_models/
├── {book_id}/
│   ├── column_boundary_model.json
│   ├── segment_boundary_model.json
│   ├── training_samples.json
│   ├── training_history.jsonl
│   └── feedback_history.jsonl
└── global/
    ├── column_boundary_model.json
    ├── segment_boundary_model.json
    ├── training_history.jsonl
    └── feedback_history.jsonl
```

### 出力CSVの形式

- `output/{book_id}/column_annotation.csv`
  - 入力CSVに `Column ID` 列が追加されます（例: COL0001, COL0002, ...）
- `output_seg/{book_id}/column_annotation.csv`
  - 上記に加えて `Segment ID` 列が追加されます（例: SEG0001, SEG0002, ...）

セグメント推定アルゴリズムの詳細は `docs/segmentation.md` を参照してください。

## モデル状態 API

- `GET /api/books/{book_id}/models/status`
  - 書籍別モデルと全書籍共通モデル、さらに実際に使われる有効ソースを返します
- `POST /api/books/{book_id}/models/train`
  - 現在の `output/` と `output_seg/` を教師データとして即時再学習します

### 履歴ファイル

- `output_models/{book_id}/training_history.jsonl`
  - 再学習のたびに、列/セグメントモデルのサンプル数と学習時 metrics を1行ずつ追記
- `output_models/{book_id}/training_samples.json`
  - その書籍の境界学習サンプルをキャッシュ。保存時の global 再学習は、全書籍 CSV の再走査ではなくこのキャッシュを集約して高速化します
- `output_models/{book_id}/feedback_history.jsonl`
  - 保存時に、初期予測に対する人手修正量（採用件数/修正件数）を1行ずつ追記

## 技術スタック

- **Backend**: FastAPI, Uvicorn
- **Frontend**: Vanilla JavaScript, HTML5 Canvas
- **画像処理**: Pillow
- **データ処理**: pandas

## ライセンス

Apache-2.0 License
