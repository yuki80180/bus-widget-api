# Schedule Monitor

北陸鉄道の時刻検索ページから金沢工業大学の時刻表を取得し、既存の `schedule.json` と比較するための初期実装です。

## 使い方

リポジトリ直下で実行します。

```bash
python monitor/fetch_schedule.py
python monitor/compare_schedule.py
```

`fetch_schedule.py` は `monitor/new_schedule.json` を保存します。
`compare_schedule.py` は `schedule.json` と `monitor/new_schedule.json` を比較し、`monitor/schedule_diff.json` を保存します。差分がある場合は終了コード `1`、差分なしの場合は `0` で終了します。

## 対象

既存アプリの方向名に合わせて、以下を取得対象にしています。

- `to_uni`: 金沢工業大学 A/C 乗り場の大学行き側
- `to_station`: 金沢工業大学 B/D 乗り場の金沢駅行き側
- `to_nakahashi`: 金沢工業大学 B/D 乗り場の中橋方面側

既存の `weekend` は1種類ですが、北陸鉄道ページは土曜日と日・祝日が分かれているため、この初期実装では `weekend` に日・祝日を使用しています。

## 変更範囲

この監視機能は `monitor/` 配下だけで完結します。既存の `app.py`、`bus.db`、`Scriptable_Scripts/` は変更しません。
