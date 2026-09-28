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

`--fail-on-diff` は `monitor/debug/route_search_compare.json` の `summary` を確認し、`added_count`、`removed_count`、`line_only_count`、`time_change_candidate_count`、`arrival_time_change_count` のいずれかが1以上なら終了コード `2` を返します。

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
- `monitor/convert_route_search_extracted.py`: 抽出結果を `route` / `day_type` / `time` / `arrival_time` / `line` / `stop` を持つ比較用形式に正規化し、`monitor/debug/route_search_normalized.json` を保存する
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
- route search: `monitor/debug/route_search_compare.json` の `routes -> route -> day_type` 配下にある以下5カテゴリ
  - `added`
  - `removed`
  - `line_only`
  - `time_change_candidates`
  - `arrival_time_changes`

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

## schedule更新案（update proposal）

`monitor/generate_update_proposals.py` は、route search比較で見つかった差分を、将来 `schedule.json` へ反映する場合の機械可読な更新案へ変換するdry-run専用runnerです。更新案を作って人間が確認できるところまでを責務とし、適用処理は持ちません。

このrunnerは保存済みの以下3ファイルだけを読みます。

- `monitor/debug/route_search_compare.json`: route search比較結果
- `schedule.json`: 更新案の現在値と整合性確認の根拠
- `monitor/route_search_reviewed_candidates.json`: 既存の人間レビュー情報

外部サイトへ再アクセスせず、`schedule.json`、`bus.db`、本番APIデータを変更しません。既定の出力先は `monitor/generated/update_proposals.json` です。`monitor/generated/` は実行ごとの生成物であり、Git管理対象ではありません。

### 生成方法

既に保存されている比較結果から生成する場合は、プロジェクトルートで次を実行します。

```bash
python monitor/generate_update_proposals.py
```

入力・出力を明示することもできます。`--output` は `monitor/generated/` 配下のJSONに限定されます。入力ファイルとの同一パス・symlink/junction・hardlinkによる別名指定も検査し、`schedule.json` や `bus.db` を出力先に指定しても拒否します。レポートは一時的なJSONファイルを経て置換し、書き込み用一時ファイルは終了時に削除します。

```bash
python monitor/generate_update_proposals.py \
  --comparison monitor/debug/route_search_compare.json \
  --schedule schedule.json \
  --reviewed monitor/route_search_reviewed_candidates.json \
  --output monitor/generated/update_proposals.json
```

Windowsで `python` がPATHにない場合は `./.venv/Scripts/python.exe monitor/generate_update_proposals.py` を使用できます。終了コードは正常生成 `0`、入力・出力エラー `1`、simulation失敗 `2` です。候補単位のvalidation errorはJSONに隔離して正常生成を継続します。

実行時は各更新案、更新案にできなかったvalidation error、最後に `Update Proposal Summary` をターミナルへ表示します。ターミナルのSummaryではstatus別・変更種別の件数、validation error数、simulation結果を確認できます。出力JSONの `summary` には、さらに入力候補数、重複抑制数、時刻変更と重複した生差分の抑制数も記録します。

### JSON出力

出力の主なフィールドは以下です。

- `proposal_version`: 更新案形式のバージョン
- `generated_at`: 更新案を生成したUTC日時
- `source`: 読み込んだ比較JSONのパス
- `apply_allowed`: 常に `false`
- `status_values`: 利用可能なstatusの一覧
- `proposals`: 安全に具体化できた更新案
- `validation_errors`: 不正または曖昧で更新案にできなかった候補
- `simulation`: メモリ上の仮適用結果と適用前後の便数
- `summary`: status別・変更種別などの集計

各proposalは、安定した `proposal_id`、`direction`、`day_type`、`change_type`、元の比較カテゴリを示す `source_category`、`status`、元比較ファイルを示す `source`、既存レビューのsnapshot、`changes` を持ちます。`changes` のoperationは、追加が `add`、削除が `remove`、時刻変更が `replace` です。`before` は必ず現在の `schedule.json` を根拠にし、`after` は候補から安全に確定できた値だけを使用します。

時刻変更には `time_context`（符号付き変更分数、同じ系統・乗り場の直前便・直後便、既存発車時刻をまたぐか）も付けます。追加確認が必要な場合は `review_reasons` に理由を記録します。

```json
{
  "proposal_version": 1,
  "generated_at": "2026-09-04T00:00:00Z",
  "source": "monitor/debug/route_search_compare.json",
  "apply_allowed": false,
  "status_values": ["pending", "needs_review", "approved", "rejected"],
  "proposals": [
    {
      "proposal_id": "<SHA-256>",
      "direction": "to_uni",
      "day_type": "weekday",
      "change_type": "time_change",
      "source_category": "time_change_candidates",
      "status": "pending",
      "source": "monitor/debug/route_search_compare.json",
      "review": {
        "matched": true,
        "candidate_status": "confirmed"
      },
      "changes": [
        {
          "operation": "replace",
          "before": {
            "time": "19:36",
            "line": "(33) 寺地・四十万行",
            "stop": "C"
          },
          "after": {
            "time": "19:46",
            "line": "(33) 寺地・四十万行",
            "stop": "C"
          }
        }
      ]
    }
  ]
}
```

### 差分カテゴリの扱い

ここでのA/B/Cは更新案へ変換できる確実性の分類であり、承認状態ではありません。

- A — `removed`: 対象便が現在の `schedule.json` に完全一致で1件だけ存在する場合、削除案へ機械的に変換できる
- B — `added`: 系統の完全表記が得られるか、現在のscheduleから系統番号・乗り場に対応する完全表記を一意に解決できる場合だけ追加案にする。解決不能または複数候補なら人間確認が必要で、更新案にはしない
- B — `time_change_candidates`: 同じdirection・day type・系統・乗り場であり、旧便と新便の対応、時刻、現在値を検証できる場合だけ `replace` 案にする。組み合わせ自体が推定を含むため、自動承認しない
- C — `line_only`: 現在の比較データだけでは置換対象と新しい完全な系統表記を安全に確定できないため、現段階では更新案を生成せずvalidation errorとして残す

`time_change_candidates` は同じ旧便・新便をそれぞれ `removed` と `added` にも含む補助カテゴリです。有効な時刻変更案を生成した場合、その構成要素は独立した追加案・削除案から除外し、同じ変更を二重生成しません。完全に同じproposal IDも1件にまとめますが、似ているだけの候補は統合しません。

同じ旧便・新便を複数の異なる組み合わせが共有する場合は、先頭候補を選ばず全候補をvalidation errorへ隔離します。不正な時刻変更の構成要素を独立したadd/removeへ戻すこともしません。`blocked_components_suppressed` に抑制数を記録し、関連する生候補もvalidation errorへ残します。同じ便に対して競合する別proposalも隔離します。

今回の入力対象はroute search比較JSONのみです。通常monitorの `update_candidates.json` の追加・削除は将来の入力adapterの対象とし、通常monitorの `line_differences` と `investigation_only`、旧調査用 `route_search_diff.json` は受け付けません。既存の取得元差異・除外系統の扱いをこのrunnerから変更しないためです。

### statusと既存レビューの関係

- `pending`: 対応する既存候補が `confirmed` で、更新案としてのvalidationにも成功した状態。変更承認済みという意味ではない
- `needs_review`: 対応する `confirmed` レビューがない、時刻移動で既存発車時刻をまたぐ、またはsimulationが失敗して追加確認が必要な状態
- `approved`: 将来、人間が更新案そのものを承認するフローのための予約状態
- `rejected`: 将来、人間が更新案そのものを却下するフローのための予約状態

generatorが `approved` や `rejected` を自動設定することはありません。既存reviewの `confirmed` は「候補を情報源で確認済み」という意味であり、schedule変更の承認ではありません。レビュー情報の正本は引き続き `monitor/route_search_reviewed_candidates.json` で、出力JSONの `review` は生成時点のsnapshotです。

reviewファイルがなければ全候補を未確認として扱います。同じreview keyに矛盾する複数エントリがある場合は入力エラーにします。生成JSONを手編集しても、再生成時に承認状態として読み戻す処理はありません。

### proposal IDと決定性

`proposal_id` は、proposal version、direction、day type、change type、`changes` からcanonical JSONを作り、そのUTF-8バイト列にSHA-256を計算して生成します。`generated_at`、status、レビュー情報、入力ファイルのパスはIDに含めません。このため、同じ更新内容からは実行プロセスに依存しない同じIDが生成されます。

proposal、validation error、Summaryの対象は安定した規則で並べ替えます。`generated_at` を除き、同じ入力から生成されるID、変更内容、配列順は同じです。

### validationとsimulation

更新案を出力する前に、少なくとも次を検証します。

- directionとday typeが現在の `schedule.json` に存在する
- day type、時刻、系統番号、乗り場が既知の形式・値である
- 削除対象と時刻変更の現在値が `schedule.json` に完全一致で1件存在する
- 追加先や時刻変更後の便が既に `schedule.json` に存在しない
- 時刻変更の旧便・新便で系統と乗り場が一致し、元の `removed` / `added` にも対応要素がある
- 時刻変更の構成要素は完全なlineを含め一致し、変更幅が既存比較処理と同じ60分以内、差分分数が正しい整数である
- 入力に明示されたroute/direction/day typeが外側の比較対象と矛盾しない
- 追加便の完全な系統表記を安全に確定できる

失敗した候補は `proposals` に混ぜず、安定した `validation_id`、`status: needs_review`、理由codeを `validation_errors` に記録します。これらは `summary.total` や `summary.by_status.needs_review` には含めず、`summary.validation_error_count` で別集計します。

生成したproposalは `schedule.json` のdeep copyへメモリ上で仮適用し、JSON化、時刻・系統・乗り場、完全重複、対象direction/day type、適用前後の便数を確認します。simulationの `status` は `passed` または `failed` です。この処理でも実ファイルや一時ファイルへscheduleを書き込みません。

仮適用前のschedule全体も検証するため、不正な元便を削除して入力不正を隠すことはできません。失敗した1変更はメモリ上でも取り消します。simulationが成功しても、未生成候補を含む全差分への対応完了やデータの鮮度を意味しません。

### テスト

```bash
python -m unittest discover -s tests -v
```

proposalテストは外部サイトや `monitor/debug/` のローカル実データに依存せず、合成データと一時ディレクトリで実行します。既存APIテストも同じコマンドで実行できます。

### sourceの鮮度

`generated_at` は更新案を生成した時刻であり、route searchデータを取得した時刻ではありません。generatorは保存済みの `monitor/debug/route_search_compare.json` を再利用するため、その元になったHTMLや比較結果が最新であることを単独では保証できません。また、比較元HTMLの取得日時は比較JSONに含まれず、`monitor/debug/` に古いページファイルが残っている場合は抽出結果へ混在する可能性があります。最新情報が必要な場合は、保存ファイルの内容も確認し、先にroute search取得・比較パイプラインを実行し直してから生成してください。

### 将来の承認・適用フロー

このJSONは、将来の明示的な承認・適用フローへ渡せる更新案です。現在はproposal生成、simulation、以下の明示選択previewまでで、apply用runner、schedule書き換え、DB再生成、commit、PR、Issue・Discord経由の承認処理はありません。statusにかかわらず `apply_allowed` は常に `false` で、`schedule.json` と `bus.db` は変更されません。

## 選択したproposalのpreview

`monitor/preview_update_proposals.py` は、生成済みproposal JSONから人間がIDで明示選択した候補だけを、現在のscheduleのメモリ上のコピーへ仮適用します。実際の変更内容を確認する機能です。実 `schedule.json`、`bus.db`、proposal JSON、レビュー情報には書き込みません。承認の保存、実適用、DB再生成、Git操作、通信・通知、定期実行への組み込みはありません。

### IDを指定して実行する

`update_proposals.json` の `proposals[].proposal_id` にある64文字の完全なIDを使用します。`--proposal-id` は必須で、複数回指定できます。全件自動選択や短縮IDの解決は行いません。以下は、保存済みデータにこれらのIDが存在する場合の例です。

```bash
python monitor/preview_update_proposals.py \
  --proposal-file monitor/generated/update_proposals.json \
  --proposal-id bf3f97c6082e60a438d95aa13ca99acddb842282c2c0a3ab5af2cbde7078afc1 \
  --proposal-id 89a49e55523db8f90b6df1e22a64235ca8f520e5e263fea769893b8657a49a87 \
  --proposal-id 27ac8fca620564524e416f2e44f990e26941c3882134b4b09370d387b2e03de8
```

Windowsで `python` がPATHにない場合は `./.venv/Scripts/python.exe` を使用します。`--proposal-file` の既定値は `monitor/generated/update_proposals.json`、`--schedule` はプロジェクトの `schedule.json`、`--output-dir` は `monitor/generated/preview/` です。別のscheduleを渡す場合も読み取り専用です。`--output-dir` は `monitor/generated/` 配下だけに指定できます。

同じIDの複数指定、入力JSON内のID重複、存在しないIDは、いずれも明確なエラーとして全体を拒否します。JSONの重複キー、非標準定数、未知version、`apply_allowed:false` でない入力、生成元simulationが失敗した入力も拒否します。

### statusと再検証

- `pending`／`approved`: IDを明示選択した場合に限ってpreview可能。`pending` は承認済みを意味しません。ID指定もpreview対象の選択という意味だけです。
- `rejected`／`needs_review`／未知status: 拒否します。残っている `review_reasons` も拒否します。
- 選択候補と関連する `validation_errors`: 隔離済みのadd/remove、時刻変更の旧便・新便、競合proposalをたどり、同じ系統番号・時刻・乗り場の候補を拒否します。関係を特定できない同一route/dayのエラーも安全側で拒否します。無関係な候補のエラーは件数をsummaryへ残します。

proposal IDは既存generatorと同じcanonical JSONからSHA-256を再計算して検証します。IDは内容の整合性確認用で、署名や永続的な承認の証明ではありません。direction、day type、操作種別、before/after、ASCIIのHH:MM、完全な系統表記、乗り場を再検証します。対象のscheduleは既存の3方向・weekday/weekend形式、各便は `time`・`line`・`stop` と任意の `arrival_time`（HH:MM/null）を受け付けます。

削除・時刻変更・到着変更ではbefore便が現在のscheduleに完全一致で1件だけ存在し、同じ系統番号の別表記による曖昧性がないことを検証します。追加・時刻変更ではafter便がまだ存在しないことを検証し、不一致は `stale/conflict` として拒否します。時刻変更はdirection/day内で完全なline・stopを維持し、変更幅1〜60分、同じ系統の既存発車時刻をまたがないことも確認します。保存された `time_context` やsimulation結果だけを信用せず、現在のscheduleで調べ直します。

同じ便の二重remove、removeとtime_change、同一便のadd重複、addとtime_change先の重複、選択した変更同士の前提依存を拒否します。現在存在する便を「別proposalで先に削除すれば追加できる」とは扱いません。選択した全件のvalidationとsimulationが成功した場合だけ出力し、一部だけ成功扱いにはしません。statusやapprovalは変更しません。

### 出力とdiff

成功時には `monitor/generated/preview/<SHA-256>/` に以下をまとめて出力します。

- `schedule.preview.json`: 仮適用後のscheduleコピー。
- `schedule.diff`: 入力とコピーのunified diff。対象ファイル名にはinput/copyを明記します。
- `summary.txt`: `[追加]`・`[削除]`・`[時刻変更]`・`[到着時刻変更]` ごとのdirection/day、変更前後の便、完全proposal IDと集計。ターミナルにも表示します。
- `summary.json`: 選択IDと元proposal/status、変更種別件数、before/after便数、simulation結果、入力・コピーのSHA-256、`apply_allowed:false`、`apply_performed:false`。

元JSONのkey順、未変更便の配列順、空白、インデント、LF/CRLF、BOM、末尾改行を可能な限り維持します。変更のあるday配列だけを組み立て直し、新便は時刻に応じて挿入します。未変更の便の相対順序は変えません。もともと空の配列への追加はその配列内だけ新たに整形します。JSON全体の再整形に頼らず、semantic summaryでも変更を確認できます。

出力先のSHA-256は全artifactの内容から決定します。入力scheduleのバイト列と選択proposalの内容が同じなら、ID指定順やproposal配列順を変えても出力内容・出力先は同一です。繰り返し実行は既存の一致した出力を再利用し、ファイルを増やしたり上書きしたりしません。入力内容や選択が異なる場合だけ別ディレクトリになります。過去のpreviewは自動削除しません。

出力先のpath traversal、symlink/junction、artifactのhardlink、入力や保護対象の別名指定を拒否します。生成物は一時ディレクトリ内に全件を書き終えてから、新しいdigestディレクトリとして公開します。失敗時は一時ファイルを片付け、既存の成功previewを変更しません。既存の同名出力に異なる内容があれば上書きを拒否します。実scheduleへのrename・replace処理はありません。

終了コードは成功・同一出力の再利用が `0`、validation/stale/conflict/入出力エラーが `1`、必須引数不足などCLI構文エラーが `2` です。失敗時に新しいcandidateを成功扱いで出力しません。以前のpreviewが残っていても、その実行の成功を示すものではありません。

### simulationの再利用とテスト

既存 `simulate_proposals()` に `include_candidate=True` を指定し、成功した場合だけメモリ上の検証済みコピーを受け取ります。既定の戻り値とgeneratorの出力schemaは変わりません。previewはこれに明示選択、stale/競合検証、書式維持、diff、出力保護を加えています。

`python -m unittest discover -s tests -v` で既存generator/APIとpreviewの回帰テストをまとめて実行できます。テストは合成データと一時ディレクトリで実行し、schedule・DBのSHA-256不変性、全体失敗、出力先保護、決定性も確認します。

## to_uni の扱い

`to_uni` は通常の停留所時刻表監視では更新候補確認対象に含めず、調査対象として扱います。現在は route search調査パイプラインと `monitor/route_search_reviewed_candidates.json` によって、確認済み候補を表示上区別しながら継続確認します。

過去の取得元差異に関するメモは、現在の運用手順とは分けて扱います。現在の運用では、route searchの抽出、正規化、比較、候補表示、レビュー済み注釈を使って確認します。

## 変更範囲

monitor は確認用のファイルを `monitor/` と `monitor/debug/` 配下に保存します。既存の `app.py`、`bus.db`、`Scriptable_Scripts/` は変更しません。DB更新や `schedule.json` の上書きは行いません。

## 到着時刻の保持・比較

公式確認済み `arrival_time`（HH:MM/null）をextract→normalize→compare→proposal→previewへ保持します。旧入力の到着欠落は扱え、登録済み到着を欠落だけで消しません。`arrival_time_changes` は到着のみの変更を表し、曖昧な対応はvalidation errorとして隔離します。runnerのfail-on-diff・fingerprintにも含めます。到着のみのproposalはneeds_reviewで、自動承認・実適用はありません。

全214便の公式根拠、方向とstopの意味、207便の確認値・7便のnull一覧、DB移行とoffline監査は [ARRIVAL_TIMES.md](ARRIVAL_TIMES.md) を参照してください。
