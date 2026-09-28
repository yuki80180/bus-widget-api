# KIT通学バス API / Web

金沢工業大学の通学バスについて、次の3便を返すAPIとスマートフォン向けWebアプリを提供します。既存のScriptableウィジェット、時刻表monitor、GitHub ActionsによるDiscord通知は同じリポジトリで管理しています。

## Webアプリ

トップページ `/` はiPhone Safariを主対象にした1画面のWebアプリです。実データに存在する次の方向を切り替えられます。

- `to_uni`: 金沢駅・中橋方面からKIT
- `to_station`: KITから金沢駅
- `to_nakahashi`: KITから中橋

次発から最大3便、次発までの残り時間、系統、行き先、のりばを表示します。本日の最終便終了後は、最大7日先まで検索した次の運行日の始発を案内します。補助表示の「今日の時刻表」では、選択中の方向について当日の全便と次の便を確認できます。運行終了と通信エラーは専用表示に切り替わり、更新ボタンまたは画面表示中の1分ごとの自動取得で最新情報を確認できます。便の検索と残り時間の計算はAPI側で行い、ブラウザ側には同じ時刻表ロジックを持ちません。

公式に確認できた便は `15:41 発 → 16:10 着` のように発着時刻を表示します。次発・後続2便・今日の時刻表・次運行日の始発が対象です。到着時刻が未確認の便は発車時刻のみ表示します。残り時間は従来どおり**発車まで**の時間です。方向見出しが到着先を示し、KIT側のstopはKIT行きでは「着」、駅・中橋行きでは「発」と表示します。Scriptableの表示は変更していません。

## ローカル開発

Windows PowerShellでは、Python 3を用意して次のように起動します。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python init_db.py
python app.py
```

ブラウザで `http://127.0.0.1:5000/` を開きます。`init_db.py` は `schedule.json` から `bus.db` を再生成するため、時刻表を変更する意図がある場合にだけ実行してください。

既存DBの便・IDを保ち、到着列だけを追加・更新する場合は `python init_db.py --migrate-arrivals` を使います。発車時刻・系統・停留所・方向・日種別・件数がscheduleと一致しないDBは変更前に拒否します。移行はtransaction内で実施し、繰り返し実行できます。旧DBのままAPIを起動した場合は到着をnullで返し、request中のschema変更は行いません。Renderのbuildでは従来どおり再生成します。

依存関係をインストール後、テストは次のコマンドで実行できます。

```powershell
python -m unittest discover -s tests -v
```

## API

`GET /api/next_bus?dir=to_uni` のように方向を指定します。`dir` を省略した場合は `to_uni` です。

既存のレスポンスフィールド `status`、`current_time`、`day_type`、`buses[].time`、`buses[].line`、`buses[].stop` は維持しています。Web表示用に次のフィールドを追加しています。

- `direction`
- `direction_detail`
- `buses[].line_number`
- `buses[].stop_name`
- `buses[].minutes_until`
- `buses[].arrival_time`（公式確認済みの `HH:MM`、未確認は `null`）
- `next_service`（本日の運行終了時のみ。次の運行日・日種別・始発情報）

`arrival_time` は `/api/next_bus`、`/api/timetable`、`next_service.bus` 共通です。`time` は発車時刻のままです。到着先は `to_uni` がKITのA/C停留所、`to_station` が金沢駅東口降車場、`to_nakahashi` が中橋です。平均所要時間や系統番号だけによる推測は使用しません。2026-09-25確認時点で214便中207便が確認済み、7便はnullです。根拠・未確認便・検証手順は [到着時刻データの記録](monitor/ARRIVAL_TIMES.md) に記載しています。

ヘルスチェックは `GET /healthz` です。

当日の全便は `GET /api/timetable?dir=to_uni` で取得できます。`dir` と省略時の既定値は `/api/next_bus` と共通です。レスポンスにはJSTの `date`、`current_time`、`day_type`、方向情報と時刻順の `buses` が含まれます。登録済みの時刻表全体が空の場合は503（`code: schedule_empty`）、DBを利用できない場合は503（`code: schedule_unavailable`）、選択した方向・日種別だけが0件の場合は200と空の `buses` を返します。

現在時刻と日付はアプリ側で日本標準時（JST）として判定します。通常の月〜金は`weekday`、土日と日本の国民の祝日は`weekend`ダイヤを使用します。祝日判定には内閣府公表データに基づく`jpholiday`を使用し、時刻表APIは`Cache-Control: no-store`を返します。

## PWA

`static/manifest.webmanifest`、192×192・512×512のPNGアイコン、Apple touch icon、iPhone向けmeta情報を設定し、ホーム画面からstandalone表示できる最小構成にしています。ライト・ダーク表示とsafe areaに対応しています。

Service Workerとオフラインキャッシュは現時点では使用しません。時刻情報の古いキャッシュを表示しないよう、API取得では `cache: no-store` を指定しています。

## デプロイ

`render.yaml` の既存Web Service設定をそのまま使用します。Renderでは `requirements.txt` のインストールと `init_db.py` をbuild時に実行し、Gunicornで `app:app` を起動します。Web専用のNode.jsビルドや追加サービスはありません。

## Monitor

時刻表monitor、route search比較、レビュー候補、自動実行、Discord通知の詳細は [monitor/README.md](monitor/README.md) を参照してください。Web/PWAはmonitorの入力ファイルや実行処理を変更しません。

到着時刻のoffline監査は `python monitor/audit_arrival_times.py`、Scriptable互換検証は `node tests/test_scriptable_compat.cjs` で実行できます。ブラウザ検証はローカルFlask起動後、PlaywrightとChromeを用意して `node tests/test_web_arrivals.cjs` を実行します（`BUS_BASE_URL`・`CHROME_PATH`で変更可能）。Playwrightは開発テストのみで、アプリのPython依存には追加していません。
