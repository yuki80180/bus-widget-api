# Schedule Monitor

北陸鉄道の公式時刻表データと既存の `schedule.json` を比較し、変更候補を人間が確認するための監視補助ツールです。

この monitor 機能は自動更新機能ではありません。`schedule.json` は自動変更しません。`bus.db` も自動変更しません。確認済み候補を記録しても、その候補を自動適用する処理はありません。

## 方針

- 外部データを取得し、既存データとの差分を確認する
- 差分や変更候補は人間が確認する
- `schedule.json`、`bus.db`、本番APIは自動更新しない
- 確認済み候補は表示上の注釈として扱い、比較結果から削除しない

monitor には大きく2種類の確認手順があります。

- 通常の停留所時刻表監視
- route search調査

## A. 通常の停留所時刻表監視

北陸鉄道の停留所時刻表から金沢工業大学の時刻表を取得し、既存の `schedule.json` と比較します。

### 通常実行

リポジトリ直下で実行します。

```bash
python monitor/run_check.py
```

`monitor/run_check.py` は以下を順番に実行します。

1. `monitor/fetch_schedule.py`
2. `monitor/compare_schedule.py`
3. `monitor/print_update_candidates.py`

### 個別実行

各ステップを個別に確認する場合は、以下の順番で実行します。

```bash
python monitor/fetch_schedule.py
python monitor/compare_schedule.py
python monitor/print_update_candidates.py
```

### 各ファイルの役割

- `monitor/fetch_schedule.py`: 北陸鉄道の停留所時刻表を取得し、`monitor/new_schedule.json` を保存する
- `monitor/compare_schedule.py`: `schedule.json` と `monitor/new_schedule.json` を比較し、`monitor/schedule_diff.json` と `monitor/update_candidates.json` を保存する
- `monitor/print_update_candidates.py`: `monitor/update_candidates.json` の `added` / `removed` を人間確認しやすい形式で表示する
- `monitor/run_check.py`: 取得、比較、候補表示をまとめて実行するrunner

`monitor/compare_schedule.py` の比較キーは `time + stop` です。`line` だけが違う便は追加・削除ではなく `line_differences` として記録します。

### 更新候補確認対象

通常の停留所時刻表監視では、更新候補確認対象を以下に絞っています。

- `to_station`: 金沢工業大学 B/D 乗り場の金沢駅行き側
- `to_nakahashi`: 金沢工業大学 B/D 乗り場の中橋方面側

`to_uni` は通常監視では更新候補確認対象に含めず、`monitor/schedule_diff.json` の `investigation_only` に調査情報を出します。

`monitor/update_candidates.json` は、手動確認しやすいように絞り込んだ候補ファイルです。

- 対象は `to_station` と `to_nakahashi`
- `added` と `removed` のみを出力
- `line_differences` は含めない
- `to_uni` は含めない
- `to_station` では `49` 番を含む `line` を候補から除外する

### weekend の扱い

既存の `schedule.json` の `weekend` は1種類ですが、北陸鉄道ページは土曜日と日・祝日が分かれています。現在の `monitor/fetch_schedule.py` では `weekend` に日・祝日を使用します。

## B. route search調査

route search調査は、発着指定検索の結果を使って、既存の `schedule.json` と比較する調査用ワークフローです。通常監視と同じく、自動更新は行いません。

現在の調査対象は平日・出発条件の以下3例です。

- 金沢駅 → 金沢工業大学
- 金沢工業大学 → 金沢駅
- 金沢工業大学 → 中橋

### runnerで実行する

最新HTML取得から実行する場合:

```bash
python monitor/run_route_search_check.py
```

保存済みHTMLを使用して再比較する場合:

```bash
python monitor/run_route_search_check.py --skip-fetch
```

差分または確認候補がある場合に終了コード `2` とする場合:

```bash
python monitor/run_route_search_check.py --skip-fetch --fail-on-diff
```

`--skip-fetch` は `monitor/research_route_search.py` だけをスキップし、保存済みの `monitor/debug/` 配下のHTMLから抽出、正規化、比較、候補表示をやり直します。

`--fail-on-diff` は `monitor/debug/route_search_compare.json` の `summary` を確認し、`added_count`、`removed_count`、`line_only_count`、`time_change_candidate_count` のいずれかが1以上なら終了コード `2` を返します。

### 処理の流れ

`monitor/run_route_search_check.py` は以下を順番に実行します。

1. `monitor/research_route_search.py`
2. `monitor/extract_route_search_debug.py`
3. `monitor/convert_route_search_extracted.py`
4. `monitor/compare_route_search_normalized.py`
5. `monitor/print_route_search_candidates.py`

処理内容は以下の流れです。

```text
route search HTML取得
↓
必要情報抽出
↓
比較用形式へ正規化
↓
既存データと比較
↓
確認候補表示
```

### 各ファイルの役割

- `monitor/research_route_search.py`: 発着指定検索のHTML、POST内容、ページ送り結果を `monitor/debug/` に保存する
- `monitor/extract_route_search_debug.py`: 保存済みHTMLから発着時刻、停留所、乗り場、系統などを抽出し、`monitor/debug/route_search_extracted.json` を保存する
- `monitor/convert_route_search_extracted.py`: 抽出結果を `route` / `day_type` / `time` / `line` / `stop` を持つ比較用形式に正規化し、`monitor/debug/route_search_normalized.json` を保存する
- `monitor/compare_route_search_normalized.py`: `schedule.json` と `monitor/debug/route_search_normalized.json` を比較し、`monitor/debug/route_search_compare.json` を保存する
- `monitor/print_route_search_candidates.py`: `monitor/debug/route_search_compare.json` を読み、人間確認用の候補一覧を表示する
- `monitor/run_route_search_check.py`: route search調査パイプラインをまとめて実行するrunner

### route search取得ファイル

`monitor/research_route_search.py` は `monitor/debug/` に以下のようなファイルを保存します。

- `01_pathway.html`: 発着指定検索フォームのHTML
- `*_stop_resolver_request.json`: `phpscript/hpjpp0500.php` に送信した停留所確認用POSTデータ
- `*_stop_resolver_response.txt`: 停留所確認レスポンス
- `*_route_search_request.json`: `pathway_timetable.php` に送信した発着検索POSTデータ
- `*_route_search_response.html`: 発着検索結果HTML
- `*_route_search_page_XX_request.json`: ページ送り用POSTデータ
- `*_route_search_page_XX_response.html`: ページ送り後の発着検索結果HTML
- `00_route_search_summary.json`: URL、POSTパラメータ、保存ファイル、ページ情報の一覧

ページ送りでは、検索結果HTML内の `form name="form1"` から input/select の値を復元し、`id="next"` または `id="next2"` の `value` を `page` として `pathway_timetable.php` にPOSTします。

`monitor/research_route_search.py` のデフォルト検索時刻は `07:00` です。必要に応じて個別に `--hour` と `--minute` を指定できます。

```bash
python monitor/research_route_search.py --hour 08 --minute 30
```

### 中間・比較JSON

- `monitor/debug/route_search_extracted.json`: route search HTMLから抽出した生寄りのデータ
- `monitor/debug/route_search_normalized.json`: `schedule.json` と比較しやすい形に正規化したデータ
- `monitor/debug/route_search_compare.json`: 既存データとroute search正規化結果の比較結果

`monitor/debug/route_search_compare.json` では、各 `route/day_type` に以下を出力します。

- `added`: 既存データにはなく、route search側に存在する便
- `removed`: 既存データにはあるが、route search側に存在しない便
- `line_only`: 時刻と乗り場は一致しているが、系統情報だけに差がある便
- `time_change_candidates`: `added` と `removed` の中から、系統や乗り場が近く、時刻変更の可能性がある組み合わせを人間確認用に表示する候補

`time_change_candidates` は、`added` / `removed` の生差分を削除したり置き換えたりしません。人間確認を補助するために、追加情報として重ねて表示する候補です。

### route search比較時の正規化

`monitor/compare_route_search_normalized.py` の比較キーは `time + stop` です。`line` は比較キーに含めません。

系統比較では、表記差による不要な差分を減らすため、系統番号を正規化して比較します。たとえば括弧付きの系統番号や数字だけの系統表記を、同じ番号として扱います。

`to_station/weekday` の比較では、正規化後の `line` が `49` の便を、既存データ側とroute search側の両方で比較対象外にします。

## GitHub Actions自動monitorとDiscord通知

自動monitorは `.github/workflows/bus-monitor.yml` の `Bus Monitor` workflowで実行します。実行方法は定期実行と手動実行の2つです。

- `schedule`: 毎日 日本時間05:17
- `workflow_dispatch`: GitHub Actions画面からの手動実行
- `cron`: `17 5 * * *`
- `timezone`: `Asia/Tokyo`
- Python: `3.13`

自動実行のrunnerは `monitor/run_automated_monitor.py` です。処理順は以下です。

```text
通常monitor実行
↓
route search monitor実行
↓
JSON読込
↓
summary件数取得
↓
論理差分payload生成
↓
canonical JSON化
↓
SHA-256 fingerprint生成
↓
前回fingerprintと比較
↓
差分内容が変化し、現在差分ありの場合だけDiscord通知
↓
state保存
```

### fingerprint仕様

fingerprintは、人間が確認すべき論理差分だけを対象にします。

- 通常monitor: `monitor/update_candidates.json` の `routes` 配下
- route search: `monitor/debug/route_search_compare.json` の `routes -> route -> day_type` 配下にある以下4カテゴリ
  - `added`
  - `removed`
  - `line_only`
  - `time_change_candidates`

以下はfingerprint対象外です。

- `summary`
- `count` / `*_count`
- `comparison_key`
- `line_comparison`
- `generated_at`
- GitHub Actions run ID
- path
- request / responseデバッグ情報

canonical JSONは以下の設定で生成します。

```python
json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

生成したcanonical JSONのUTF-8バイト列に対してSHA-256を計算します。

### 状態管理

前回fingerprintは `.monitor_state/last_diff_hash.txt` に保存します。GitHub Actionsでは `.monitor_state/` をcacheします。

- cache path: `.monitor_state/`
- cache key: `bus-monitor-state-${{ github.ref_name }}-${{ github.run_id }}`
- restore-keys:
  - `bus-monitor-state-${{ github.ref_name }}-`
  - `bus-monitor-state-`

### 通知条件

Discord通知は「差分内容が変化し、現在差分あり」の場合だけ行います。

- 前回状態なし + 差分なし: 通知なし、保存
- 前回状態なし + 差分あり: Discord通知、送信成功後に保存
- 同一差分: 通知なし、保存
- 異なる差分 + 現在差分あり: Discord通知、送信成功後に保存
- 異なる差分 + 現在差分なし: 通知なし、保存

差分ありから差分なしになった後、同じ差分が再発した場合は、前回fingerprintと異なるため再通知します。

Discord通知が必要な場合に送信へ失敗したときは、stateを保存せず非0終了します。通知不要時は `DISCORD_WEBHOOK_URL` が未設定でも正常終了できます。

### Discord通知

Discord Webhook URLはGitHub Actions repository secretの `DISCORD_WEBHOOK_URL` から取得します。

通知は確認依頼です。`schedule.json` / `bus.db` は自動更新しません。

Discord Webhookリクエストでは、Cloudflare 1010対策としてUser-Agentを明示します。

```text
User-Agent: DiscordBot (https://github.com/yuki80180/bus-widget-api, 1.0)
```

### 実環境確認済み

以下は実環境のGitHub Actionsで確認済みです。

- monitor自動実行
- route search自動実行
- 差分summary取得
- fingerprint生成
- Discord通知
- state保存
- Actions cache復元
- 前回fingerprint比較
- 同一差分通知抑制

## レビュー済み候補管理

`monitor/route_search_reviewed_candidates.json` は、人間がすでに確認したroute search候補を記録し、再表示時に確認済みか未確認かを区別するための管理ファイルです。

`monitor/print_route_search_candidates.py` は、このファイルが存在する場合だけ読み込み、候補表示に以下の注釈を付けます。

- `[未確認]`: まだレビュー記録がない候補
- `[確認済み]`: レビュー済み候補
- `[確認済み: 時刻変更対応]`: 時刻変更候補として確認済みの候補に対応する `added` / `removed`

レビュー済み候補は表示から削除しません。確認済み注釈は、毎回同じ候補を再確認する負担を減らすための表示上の区別です。

レビュー済み候補を記録しても、以下は変わりません。

- Summary件数
- `added` / `removed` などの生差分
- `monitor/debug/route_search_compare.json` の比較結果

### スキーマ例

`monitor/route_search_reviewed_candidates.json` は以下の形式です。

```json
{
  "schema_version": 1,
  "scope": "route_search",
  "reviewed": [
    {
      "route": "to_uni",
      "day_type": "weekday",
      "type": "added",
      "key": {
        "time": "16:53",
        "line_normalized": "49",
        "stop": "A"
      },
      "status": "confirmed",
      "source": "hokutetsu_busstop_timetable",
      "reviewed_note": "公式停留所時刻表で確認済み"
    },
    {
      "route": "to_uni",
      "day_type": "weekday",
      "type": "time_change",
      "key": {
        "old_time": "19:36",
        "new_time": "19:46",
        "line_normalized": "33",
        "stop": "C"
      },
      "status": "confirmed",
      "source": "hokutetsu_busstop_timetable",
      "reviewed_note": "公式停留所時刻表で確認済み"
    }
  ]
}
```

`removed` の確認済み候補は、`type` を `removed` にし、`key.time`、`key.line_normalized`、`key.stop` で記録します。

## to_uni の扱い

`to_uni` は通常の停留所時刻表監視では更新候補確認対象に含めず、調査対象として扱います。現在は route search調査パイプラインと `monitor/route_search_reviewed_candidates.json` によって、確認済み候補を表示上区別しながら継続確認します。

過去の取得元差異に関するメモは、現在の運用手順とは分けて扱います。現在の運用では、route searchの抽出、正規化、比較、候補表示、レビュー済み注釈を使って確認します。

## 変更範囲

monitor は確認用のファイルを `monitor/` と `monitor/debug/` 配下に保存します。既存の `app.py`、`bus.db`、`Scriptable_Scripts/` は変更しません。DB更新や `schedule.json` の上書きは行いません。
