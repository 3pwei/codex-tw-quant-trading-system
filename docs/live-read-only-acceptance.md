# Live Read-Only Recovery 驗收與 Soak

本程序驗證 production broker connection、callback audit、initial/periodic reconciliation
與 Recovery Lock。它不授權實盤交易；整段驗收期間
`LIVE_TRADING_ENABLED=false`，submit、cancel、replace 計數必須為零。

## 前置條件

1. 由兩人核對 execution container 無 published port、Caddy route 或 Cloudflare route。
2. 確認 Public Application 未取得 `SJ_*`、CA 或完整 account ID；CA 與
   `execution.env` 權限為 `0600`。
3. 設定明確的 Shioaji read-only confirmation、allowlisted account 與 instrument map。
4. 券商帳號應無平台未知的委託或部位。若有，預期結果是 lock，不得為了通過驗收刪除
   audit 或改寫 local state。
5. 保存部署 commit、執行人、帳號遮罩、session 日期與 health evidence 路徑。

## 啟動驗收

- 重啟 `execution-worker`，確認第一個 durable recovery state 是 `LOCKED`，generation
  比上一輪大。
- 觀察狀態依序到 `BROKER_CONNECTING`、`BROKER_READ_ONLY_READY`、`RECONCILING`；
  truth 完全一致時才可到 `READY_READ_ONLY`。
- 確認 `ordering_enabled=false`、`external_order_calls=0`、
  `external_cancel_calls=0`，Dashboard 沒有 Live 交易按鈕。
- 開啟 `/settings/`，確認只顯示遮罩帳號與 cached health；請求本身不得增加 broker
  snapshot/read 計數。

## Session soak

至少各執行一次完整日盤與完整夜盤。每 5 分鐘保存一次 health snapshot，並在 session
開始、收盤、worker restart 及任何 fault injection 前後保存額外 snapshot。

| Evidence | 應保存欄位 |
|---|---|
| Broker uptime | connected、client state、CA ready |
| Callback | received、reconciled、unmatched、failed、dropped、queue high watermark |
| Reconciliation | attempts、success/failure、overlap skip、average/max latency |
| Snapshot | last success、age、orders/fills/positions counts（audit side） |
| Recovery | status、issue codes、generation、state transitions |
| Runtime | restart count、CPU、memory、task shutdown result |
| Safety | submit/cancel/replace SDK call counters |

## Fault injection

在 Fake SDK／staging 執行：broker disconnect、broker API timeout、reconciliation timeout、
callback queue full、duplicate callback、out-of-order callback、snapshot exception、position
mismatch、unknown external order、process restart 與 stale completion。每項都必須：

1. worker 或該 account 進入 `LOCKED`／`DEGRADED`；
2. 其他 broker/account 不受影響；
3. issue code 與時間可追溯，error 不含 secret；
4. 不 import order、不覆寫 position、不 flatten、不 retry submit；
5. 後續成功 reconciliation 才能恢復 `READY_READ_ONLY`。

## Acceptance targets

| 指標 | 目標 |
|---|---:|
| Real submit calls | 0 |
| Real cancel calls | 0 |
| Real replace calls | 0 |
| Unexpected position mismatch | 0 |
| Unexpected broker order | 0 |
| Dropped callback | 0 |
| Unresolved reconciliation | 0 |
| Secret leak | 0 |

任一目標不符合即停止 soak、保留 lock 與 evidence，不得手動改成 READY。Position mismatch、
unknown broker order 與 `UNKNOWN` without broker ID 需要後續人工 resolution workflow；
本 PR 不提供自動修復。
