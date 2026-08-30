# KIT通学バス API / Web

金沢工業大学の通学バスについて、次の3便を返すAPIとスマートフォン向けWebアプリを提供します。既存のScriptableウィジェット、時刻表monitor、GitHub ActionsによるDiscord通知は同じリポジトリで管理しています。

## Webアプリ

トップページ `/` はiPhone Safariを主対象にした1画面のWebアプリです。実データに存在する次の方向を切り替えられます。

- `to_uni`: 金沢駅・中橋方面からKIT
- `to_station`: KITから金沢駅
- `to_nakahashi`: KITから中橋

次発から最大3便、次発までの残り時間、系統、行き先、のりばを表示します。運行終了と通信エラーは専用表示に切り替わり、更新ボタンまたは画面表示中の1分ごとの自動取得で最新情報を確認できます。便の検索と残り時間の計算はAPI側で行い、ブラウザ側には同じ時刻表ロジックを持ちません。

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

ヘルスチェックは `GET /healthz` です。

現在時刻と日付はアプリ側で日本標準時（JST）として判定します。通常の月〜金は`weekday`、土日と日本の国民の祝日は`weekend`ダイヤを使用します。祝日判定には内閣府公表データに基づく`jpholiday`を使用し、`/api/next_bus` は`Cache-Control: no-store`を返します。

## PWA

`static/manifest.webmanifest`、192×192・512×512のPNGアイコン、Apple touch icon、iPhone向けmeta情報を設定し、ホーム画面からstandalone表示できる最小構成にしています。ライト・ダーク表示とsafe areaに対応しています。

Service Workerとオフラインキャッシュは現時点では使用しません。時刻情報の古いキャッシュを表示しないよう、API取得では `cache: no-store` を指定しています。

## デプロイ

`render.yaml` の既存Web Service設定をそのまま使用します。Renderでは `requirements.txt` のインストールと `init_db.py` をbuild時に実行し、Gunicornで `app:app` を起動します。Web専用のNode.jsビルドや追加サービスはありません。

## Monitor

時刻表monitor、route search比較、レビュー候補、自動実行、Discord通知の詳細は [monitor/README.md](monitor/README.md) を参照してください。Web/PWAはmonitorの入力ファイルや実行処理を変更しません。
