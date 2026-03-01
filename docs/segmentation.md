# セグメント分割アルゴリズム仕様

本ドキュメントは、列アノテーション（Column ID）からページ内セグメント（Segment ID）を推定し、矩形領域（bbox）として扱える形で分割するアルゴリズム仕様を定義する。

## 目的

- Column ID（読み順）に基づき、ページ内の列集合をセグメントに分割する。
- セグメントは矩形領域（外接矩形）で表現できること。
- セグメント同士の重なり（面積としてのoverlap）をなるべく小さくすること。
- 縦に積まれた列（同一X帯で上下に分離）を別セグメントとして扱うこと。

## 入力

- ページ内の文字集合（座標CSVの該当ページ行）
  - 列: `Image, Unicode, X, Y, Width, Height, Char ID, Block ID, ...`
- 列アノテーション
  - 各文字に `Column ID`（例: `COL0001`）が付与されている

## 出力

- `Segment ID`（例: `SEG0001`）を列単位で推定し、各文字行に付与する。
- 出力先（派生CSV）:
  - `output_seg/{book_id}/column_annotation.csv`
  - 入力CSVの全列を保持し、追加列として `Column ID`, `Segment ID` を持つ。

## 前提

- Column ID はページ内の読み順で連番（`COL0001`, `COL0002`, ...）である。
- 列bboxは各列に属する文字bboxの外接矩形で近似できる。
- ページ内の列は概ね縦書きで並び、同一X帯に上下で積まれる場合がある。

## 用語と定義

### 列bbox（ColumnBox）
列 `COLxxxx` に属する全文字bboxから外接矩形を作る。

- `left  = min(X)`
- `right = max(X + Width)`
- `top   = min(Y)`
- `bottom= max(Y + Height)`
- `cw = max(1, right - left)`
- `ch = max(1, bottom - top)`

### セグメントbbox
セグメントに含まれる列bboxの外接矩形。

### X重複率（x_overlap_ratio）
2つの列bboxのX方向の重なりを、幅の小さい方で正規化する。

- `x_inter = max(0, min(a.right, b.right) - max(a.left, b.left))`
- `x_overlap_ratio = x_inter / min(a.cw, b.cw)`

### Y重複率（y_overlap_ratio）
2つの列bboxのY方向の重なりを、高さの小さい方で正規化する。

- `y_inter = max(0, min(a.bottom, b.bottom) - max(a.top, b.top))`
- `y_overlap_ratio = y_inter / min(a.ch, b.ch)`

### 矩形の重なり比（overlap_ratio）
2つの矩形の交差面積を、面積の小さい方で正規化する。

- `inter = inter_w * inter_h`
- `overlap_ratio = inter / min(area(a), area(b))`

## しきい値（現行）

- `x_th = 0.6`
  - 同一セグメント内で「同一X帯」とみなす強い重なり
- `stacked_x_th = 0.35`
- `stacked_y_th = 0.1`
  - 「縦積み」検出（Xはそこそこ重なるが、Yがほぼ重ならない）
- `rect_overlap_th_closed = 0.1`
  - 過去に確定したセグメント矩形との干渉許容
- `rect_overlap_th_future = 0.05`
  - 将来列（先読み）との干渉判定

## アルゴリズム概要

Column ID順（読み順）に列bboxを走査し、貪欲にセグメントを構成する。

### 分割条件
次のいずれかに該当した場合、現在セグメントを確定し、新しいセグメントを開始する。

1. closedセグメントとの重なり（大きな重なりのみNG）
- `overlap_ratio(cand_rect, any_closed_rect) > rect_overlap_th_closed`

2. 同一セグメント内のX帯衝突（縦積み含む）
- 現在セグメントに含まれる任意の列 `b` について、
  - `x_overlap_ratio(b, cur) >= x_th` なら衝突
  - もしくは `x_overlap_ratio(b, cur) >= stacked_x_th` かつ `y_overlap_ratio(b, cur) <= stacked_y_th` なら縦積み衝突

3. 先読み（future）
- `cand`（現在セグメント+cur）の任意の列とX帯衝突する future 列が存在し、かつ
  - `overlap_ratio(cand_rect, future_rect) > rect_overlap_th_future`

### 疑似コード

```python
segments = []
closed_rects = []

current = [COL0001]
current_rect = rect_for_cols(current)

for cur in COL0002..COLNNNN:
    cand = current + [cur]
    cand_rect = rect_for_cols(cand)

    if overlap_with_closed(cand_rect) > rect_overlap_th_closed:
        close(current)
        current = [cur]
        continue

    if conflicts_x_band(current, cur):
        close(current)
        current = [cur]
        continue

    if intersects_future(cand, cand_rect):
        close(current)
        current = [cur]
        continue

    current = cand

close(current)

# segment ids: SEG0001.. in page order
```

## 実装箇所

- Backend: `main.py`
  - `save_annotations()` 保存時に `output_seg/{book_id}/column_annotation.csv` をページ単位更新
- Frontend: `static/app.js`
  - 表示切替トグルで「列表示/セグメント表示」を切替
  - セグメント表示は同一ロジックで列からセグメントを組み、セグメントbboxを描画

## 既知の意図

- 「左へ移動＋上に戻る」を直接の分割条件にしない。
  - 読み順の推定に依存しやすく、副作用で不必要な分割が起きやすいため。
- 小さな矩形交差は許容する。
  - 厳密交差だと列bboxのノイズで過分割しやすい。
- 縦積み列は必ず別セグメントにする。
  - 同じX帯でも上下に分離している場合、同一セグメント矩形にすると重なりが増えやすい。
