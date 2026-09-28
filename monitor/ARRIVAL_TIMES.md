# 到着時刻の根拠と仕様（2026-09-25）

`arrival_time` は公式検索結果で確認できた `HH:MM` または `null`。平均所要時間・固定加算・系統番号だけ・前後便からの推測は使わない。`time` は従来の発車時刻であり、今回の導入では既存214便のtime/line/stop/direction/day_type、並び順、DBのIDを変更していない。

## 発着地点とstop

| direction | timeの発車地点 | arrival_timeの到着地点 | stopの意味 |
|---|---|---|---|
| to_uni | 金沢駅東口（公式に対応できた便） | 金沢工業大学 A/C | KIT側の降車停留所 A=正門向い、C=四十万方向 |
| to_station | 金沢工業大学 B/D | 金沢駅東口降車場 | KIT側の乗車停留所 B=正門前、D=四十万から |
| to_nakahashi | 金沢工業大学 B/D | 中橋 | KIT側の乗車停留所 B/D |

既存のKIT行き見出し「金沢駅・中橋方面」は維持。到着確認値を登録できた便はすべて金沢駅発の公式検索に一意対応した。未対応便の出発地を推測して別停留所に置き換えていない。weekdayは通常平日、weekendは既存仕様の土日・日本の祝日。

## 公式ソースと取得

- [北陸鉄道公式発着指定検索](https://arj.hokutetsu.co.jp/timetable/pathway.php) の `pathway_timetable.php` による直通検索。
- 最初に保存済み `monitor/debug/*_route_search_page_*_response.html`、extracted/normalized/compare、request/summaryを調査。既に `depart_time` / `arrive_time` / 発着停留所 / pole / line / via が存在し、平日122便に一意対応した。
- 保存データのダイヤIDは `136,20260401`。保存フォームが7月4日までと明示していたこと、および週末分が不足したため、現行の `138,20260705` を2026-09-25に確認。フォームは9月30日までのダイヤとして表示。10月1日以降のダイヤは今回の反映対象ではない。
- 既存 `research_route_search.py` のSearchCase/runを再利用し、3方向×平日/土曜/日祝を取得。00:00指定は公式が結果なしを返したため、標準の07:00指定・先頭へのページ戻し・最終ページまでの取得で再確認した。完全性はページ総件数と抽出件数の一致で確認した。
- 成功rawは `monitor/debug/arrival_20260925_0700/`（KIT行き日祝）と `monitor/debug/arrival_20260925_remaining/`（残り8条件）。失敗した00:00検索結果と旧ダイヤを混ぜていない。全9条件299行の公式事実を [arrival_time_evidence.json](arrival_time_evidence.json) に保存した。検索条件、取得日、ダイヤID、rawページのSHA-256、各行の発着時刻・停留所・pole・系統・経由を保持する。raw HTMLは既存方針でGit管理外。
- [指定されたKIT 2026年度PDF](https://www.kanazawa-it.ac.jp/career/2026_bus.pdf) は就職活動支援の高速バス補助資料であり、通学路線の到着時刻の根拠に採用していない。

対応キーは方向・日種別・発車時刻・正規化系統番号・KIT側pole。加えて公式の発着停留所が方向と整合すること、対応行が1件であることを確認する。`weekend`は土曜と日祝の両方が同一便・同一到着時刻の場合だけ登録する。対応不明・複数対応・土日不一致ならnull。

## 全便監査

| direction / day_type | 全便 | 公式確認済み | null（未確認） | 曖昧 | 不正時刻 | 重複 | DB不一致 |
|---|---:|---:|---:|---:|---:|---:|---:|
| to_uni / weekday | 55 | 48 | 7 | 0 | 0 | 0 | 0 |
| to_uni / weekend | 35 | 35 | 0 | 0 | 0 | 0 | 0 |
| to_station / weekday | 50 | 50 | 0 | 0 | 0 | 0 | 0 |
| to_station / weekend | 32 | 32 | 0 | 0 | 0 | 0 | 0 |
| to_nakahashi / weekday | 24 | 24 | 0 | 0 | 0 | 0 | 0 |
| to_nakahashi / weekend | 18 | 18 | 0 | 0 | 0 | 0 | 0 |
| 合計 | 214 | 207 | 7 | 0 | 0 | 0 | 0 |

nullは以下のKIT行き平日7便。同一発車時刻・系統・KIT poleの公式便がないため、別便の到着を流用していない。

| 発車 | line | stop |
|---|---|---|
| 12:51 | (33) 寺地 | A |
| 16:26 | (39) 泉野 | A |
| 17:26 | (39) 泉野 | A |
| 19:36 | (33) 寺地・四十万行 | C |
| 20:11 | (33) 寺地・四十万行 | C |
| 20:41 | (33) 寺地・四十万行 | C |
| 21:26 | (33) 寺地・四十万行 | C |

既存monitorが検出している発車時刻・便数等の差分は今回適用していない。確認済み207便に日跨ぎはなく、24時以降の拡張表現は導入していない。validationはASCII HH:MM/nullを検証し、単に到着が発車より小さいことだけでは不正扱いしない。

## DBと再検証

`python init_db.py --migrate-arrivals` は既存scheduleとの全便一致を確認後、必要な場合のみ `ALTER TABLE bus_schedule ADD COLUMN arrival_time TEXT NULL` し、到着値だけを更新する。transactionでrollback可能、繰り返し実行可能、request中は実行しない。今回のbus.dbにはこの移行を使用し、元のID・列値・sqlite_sequence・既存schema（新列を除く）を保持した。新規buildでは従来の `python init_db.py` がarrival列を含むDBを生成する。旧schemaでもAPIは到着nullで稼働する。

`python monitor/audit_arrival_times.py` は通信・schedule/DB更新なしで全便を再照合し、上記集計、null一覧、日跨ぎ、不一致をJSONで出力する。evidence内のbaseline SHA-256とテストは、到着以外のschedule内容・配列順、DBのIDを含む元の6列が変化していないことも検証する。

## monitor / proposal / preview

extractは旧 `arrive_time` を残して `arrival_time` を追加。normalizeは新キーと旧キーを読め、到着のない旧入力はnull。発着地点が方向と一致しない到着は採用せず、不正時刻は拒否する。既存定期runnerの調査範囲は平日3方向のまま。週末の今回の証跡は独立して監査する。

compareは既存added/removed/line_only/time_change_candidatesを維持し、`arrival_time_changes` / `arrival_time_change_count` を追加。同じ便の到着変更・nullから公式確認値への補完を検出する。公式入力の到着欠落だけでは登録済み値の削除を提案しない。同じ便に複数の到着候補がある場合は曖昧としてproposal validation errorへ隔離する。runnerのfail-on-diff、表示、通知fingerprintにも新カテゴリを含む（今回通知は送信していない）。

generatorはadd/remove/time_changeのarrivalを保持し、変更前は現scheduleで再確認する。発車が変わった便に元の到着を持ち越さず、新便の公式到着またはnullを使う。到着のみの変更は `arrival_time_change` / replaceとして生成し、needs_reviewとする。到着を含む発車変更も旧departureレビューだけでpendingにせず追加レビュー対象。proposal IDにarrivalを含め、stale arrival・競合・不正入力を拒否する。approvedへの自動遷移はない。

previewは旧3キー便とarrival付き便を受け付け、明示選択・従来のstatus条件・stale/conflict再検証を維持する。候補scheduleコピーと `[到着時刻変更]` の前後summaryを出力する。実schedule/DBへの自動適用はない。mainへ正式統合した `73c0ce5` のpreview基盤を使用し、到着対応だけを追加している。元の `feature/monitor-proposal-preview` と `feature/web-arrival-times` は参照元として保持している。

## 検証コマンド

```text
python -m unittest discover -s tests -v
python monitor/audit_arrival_times.py
python monitor/run_route_search_check.py --skip-fetch
python monitor/generate_update_proposals.py
node tests/test_scriptable_compat.cjs
node tests/test_web_arrivals.cjs
python -m pip check
```

Web検証にはローカルFlask、Playwright、Chromeが必要。アプリに新依存は不要。`BUS_BASE_URL`（既定http://127.0.0.1:5000）と`CHROME_PATH`でテスト環境を指定できる。ブラウザ画像は `monitor/generated/web-arrival/` に出力する。iPhone Safari/PWA実機の目視確認は別途必要であり、デスクトップChromeの幅指定を実機確認とは扱わない。

2026-09-25の実行結果: main baselineは70件PASS。旧previewの既存68件を引き継ぎ、到着対応のPythonテスト29件を追加して167/167 PASS。Scriptableは36ケース（旧/新レスポンス計72実行）PASS。Chromeは320/375/390/768/1280px × light/darkの10ケースPASS。3方向、発着/null、次発/後続2便/時刻表/翌始発、長文overflow、方向保存、countdown、Escape/focus、60秒更新、visibility、no-store/AbortSignalを検証した。Python syntax 23ファイル、JS syntax、manifest JSON、jpholiday/app:app import、pip checkもPASS。

保存済みmonitorのoffline再実行は成功し、既存差分（added 6 / removed 7 / line_only 0 / time_change_candidates 3）を維持、arrival差分0。`--fail-on-diff` の終了2はこの既存差分を示す期待値。proposalは8案、validation隔離2件、simulation PASS。pending 5案のpreviewもPASSし、実schedule/DBは不変。

scheduleの到着フィールドを除いたバイト列が変更前と完全一致し、DBの元の6列・sqlite_sequenceも一致。元のscheduleはGit内もCRLFなので、差分の空白検証は `git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --check` を使用しPASSした。CRを空白と誤検出させるための全体改行変換は行っていない。
