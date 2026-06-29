# Scriptable Scripts

このフォルダには、iOSのScriptableで使うバス時刻表ウィジェット用スクリプトを保存しています。

## ファイル

- `bus_to_uni.js`: 大学行き
- `bus_to_station.js`: 金沢駅行き
- `bus_to_nakahashi.js`: 中橋行き

## 使い方

1. iPhoneでScriptableを開く
2. 使いたい方面のファイル内容をScriptableの新規スクリプトに貼り付ける
3. ホーム画面にScriptableの中サイズウィジェットを置く
4. Widget設定で該当スクリプトを選ぶ

各スクリプトには、ウィジェット上の「更新」ボタンと、更新後にホーム画面へ戻る `App.close()` を入れています。

## API

- Webアプリ: https://bus-widget-api.onrender.com/
- API: https://bus-widget-api.onrender.com/api/next_bus
