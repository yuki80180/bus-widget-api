# Scriptable Scripts

このフォルダには、iOSのScriptableで使うバス時刻表ウィジェット用スクリプトを保存しています。

## ファイル

- `bus_to_uni.js`: 金沢駅・中橋方面 → KIT（`to_uni`）
- `bus_to_station.js`: KIT → 金沢駅（`to_station`）
- `bus_to_nakahashi.js`: KIT → 中橋（`to_nakahashi`）

3ファイルはそれぞれ単独でScriptableへ貼り付けられる構成です。外部モジュールや`widgetParameter`は使用しません。

## 新規導入

1. iPhoneでScriptableを開く
2. 使いたい方面のファイル内容をScriptableの新規スクリプトに貼り付ける
3. ホーム画面にScriptableウィジェットを置く
4. Widget設定で該当スクリプトを選ぶ

## 既存ウィジェットの更新

1. 現在使っている方面と同じScriptableスクリプトを開く
2. コードの一部分ではなく、該当ファイルの内容全体で置き換える
3. Scriptable上のスクリプト名はそのまま保存する
4. Scriptableアプリ内のプレビューで表示を確認する

同じスクリプト名を維持して全置換する場合、ホーム画面ウィジェットのスクリプト再選択や`widgetParameter`の再設定は不要です。別名の新規スクリプトとして保存した場合だけ、Widget設定で選び直してください。

このリポジトリにはiCloudのScriptableフォルダへ自動配置・同期する仕組みはありません。既存運用どおり、Scriptableアプリでファイル全体を貼り替えてください。

## 表示と更新

- Small: 次発1便を、時刻と残り時間優先で表示
- Medium: 次発と次々発の2便を表示
- Large: 最大3便を表示
- 残り時間は「まもなく」「あとN分」「あとN時間N分」で表示
- 本日の運行終了後、`next_service`があれば次の運行便を表示
- ウィジェット全体のタップで本番Web/PWAを開く
- Medium / Largeの「更新」をタップするとScriptableを再実行
- `refreshAfterDate`は5分後を更新希望時刻として設定（実際の更新時刻はiOSが決定）
- API timeoutは30秒。keep-alive目的の追加通信は行わない

既存の固定ダーク配色を維持しているため、iPhoneのライト・ダーク設定にかかわらず同じ高コントラスト表示になります。

アプリ内実行時はMediumをプレビューします。ホーム画面ではWidget設定のSmall / Medium / Largeに合わせて表示します。

## API

- Webアプリ: https://bus-widget-api.onrender.com/
- API: https://bus-widget-api.onrender.com/api/next_bus

ウィジェットは引き続き`/api/next_bus`のみを利用します。今日の全時刻表はWeb/PWAで確認し、Scriptableから`/api/timetable`は利用しません。
