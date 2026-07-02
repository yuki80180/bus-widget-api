# Schedule Monitor

北陸鉄道の時刻検索ページから金沢工業大学の時刻表を取得し、既存の `schedule.json` と比較するための初期実装です。

## 使い方

リポジトリ直下で実行します。取得、比較、候補表示をまとめて確認する場合は以下を使います。

```bash
python monitor/run_check.py
```

`run_check.py` は `fetch_schedule.py`、`compare_schedule.py`、`print_update_candidates.py` を順番に実行します。`monitor/update_candidates.json` の `added_count` と `removed_count` がどちらも `0` の場合は、最後に `更新候補なし` と表示します。

個別に実行する場合は以下の順番で実行します。

```bash
python monitor/fetch_schedule.py
python monitor/compare_schedule.py
python monitor/print_update_candidates.py
```

`fetch_schedule.py` は `monitor/new_schedule.json` を保存します。
`compare_schedule.py` は `schedule.json` と `monitor/new_schedule.json` を比較し、`monitor/schedule_diff.json` と `monitor/update_candidates.json` を保存します。
`print_update_candidates.py` は `monitor/update_candidates.json` の `added` / `removed` だけを、手動確認しやすい形式で表示します。

比較は `time + stop` を主キーにします。`line` だけが違う便は追加・削除ではなく `line_differences` として別に記録します。差分がある場合は終了コード `1`、差分なしの場合は `0` で終了します。

## 発着指定検索の調査

発着指定検索フォームを使った取得方法は調査中です。既存の `fetch_schedule.py` とは別に、調査用スクリプトでHTMLとPOST内容を保存します。

```bash
python monitor/research_route_search.py
```

このスクリプトは `monitor/debug/` を作成し、以下を保存します。

- `01_pathway.html`: 発着指定検索フォームのHTML
- `*_stop_resolver_request.json`: `phpscript/hpjpp0500.php` に送信した停留所確認用POSTデータ
- `*_stop_resolver_response.txt`: 停留所確認レスポンス
- `*_route_search_request.json`: `pathway_timetable.php` に送信した発着検索POSTデータ
- `*_route_search_response.html`: 発着検索結果HTML
- `00_route_search_summary.json`: URL、POSTパラメータ、保存ファイルの一覧

調査対象は平日・出発条件で、以下の3例です。

- 金沢駅 → 金沢工業大学
- 金沢工業大学 → 金沢駅
- 金沢工業大学 → 中橋

デフォルトの検索時刻は `07:00` です。必要に応じて `--hour` と `--minute` を指定できます。

```bash
python monitor/research_route_search.py --hour 08 --minute 30
```

この調査スクリプトは自動更新には使いません。`schedule.json` や `bus.db` は変更しません。

## update_candidates.json

`monitor/update_candidates.json` は、DB更新候補を目視しやすくするための絞り込みファイルです。

- 対象は `to_station` と `to_nakahashi` のみ
- `added` と `removed` のみを出力
- `line_differences` は含めない
- `to_uni` は含めない
- `to_station` では `49` 番を含む `line` を候補から除外する

直近の差分で出ていた `to_station/weekday` の `07:25`、`B` 乗り場、`(49/39) 泉野・金沢駅行` は、49番を含むため更新候補から除外します。

## 更新対象

現時点で自動更新の比較対象にするのは以下だけです。

- `to_station`: 金沢工業大学 B/D 乗り場の金沢駅行き側
- `to_nakahashi`: 金沢工業大学 B/D 乗り場の中橋方面側

`to_uni` は取得元が既存 `schedule.json` とずれている可能性が高いため、自動更新対象から外し、`schedule_diff.json` の `investigation_only` に調査情報だけを出します。

## to_uni 調査メモ

直近の調査では、既存 `schedule.json` と新取得結果の時刻一致数がかなり少ない状態でした。

- `to_uni/weekday`: 既存55件、新取得53件、同じ時刻は2件
- `to_uni/weekend`: 既存35件、新取得10件、同じ時刻は1件

既存の `to_uni` は金沢駅方面などから大学へ向かう「大学行き」の便を持っている一方、現在の取得処理は金沢工業大学 A/C 乗り場の時刻表から、大学停留所を出発して先へ進む便を拾っている可能性があります。

また `to_uni/weekend` は新取得結果に C 乗り場が出ておらず、既存の A/C 構成とも合っていません。土曜と日・祝日の違いだけでは、35件から10件への減少は説明できません。

## weekend の扱い

既存の `weekend` は1種類ですが、北陸鉄道ページは土曜日と日・祝日が分かれています。この初期実装では `weekend` に日・祝日を使用しています。

調査時点では `to_station` と `to_nakahashi` は土曜・日祝の時刻が同じで、既存 `schedule.json` の `weekend` とも時刻が一致していました。

## 変更範囲

この監視機能は `monitor/` 配下だけで完結します。既存の `app.py`、`bus.db`、`Scriptable_Scripts/` は変更しません。DB更新や `schedule.json` の上書きは、この段階では行いません。
- `*_route_search_next_page_request.json`: 最初の発着検索結果HTMLから復元したフォームデータに `arrow=next` と `page` を追加した次の件ページ用POSTデータ
- `*_route_search_next_page_response.html`: 次の件ページの発着検索結果HTML

「次の件」ページの調査取得では、最初の検索結果HTML内の `form name="form1"` から input/select の値を復元し、`id="next"` または `id="next2"` の `value` を `page` として `pathway_timetable.php` にPOSTする。これにより、例として `uni_to_nakahashi_weekday` では `2～6／24件` から `7～11／24件` に進むことを確認した。