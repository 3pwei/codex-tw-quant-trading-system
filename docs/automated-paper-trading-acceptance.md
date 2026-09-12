# Automated Paper Trading Production Acceptance

本程序驗證 `/trade/` 的 Paper Auto 操作與復原可靠性。Automated Paper Trading 不等於
Live Trading；production 必須保持 Shioaji quote-only、CA 未載入、真實 order client
未建立，以及 `DisabledExecutionWorker`。

## 前置條件

1. 記錄 PR、commit、部署 run、操作者、開始時間與預計結束時間。
2. 確認 `/api/admin/health` 顯示 market、paper recovery 與 runtime recovery healthy。
3. 準備一個 `trading_mode=paper` 且有 `orders.paper` 的 Trader，以及一個 Researcher。
4. 保存測試前 orders／fills／positions 數量與 runtime metrics baseline。
5. Researcher 應只能 Observe；任何 ARM 嘗試均須回傳 403。

## 四小時 Soak 程序

| 時段 | 操作 | 驗證重點 |
|---|---|---|
| 0:00–0:30 | 建立 snapshot、核對參數與風控、ARM | closed K 才有 decision；next open 才成交 |
| 0:30–1:15 | 保持 armed，重複送入相同 closed bar／decision 測試事件 | 沒有重複 order 或 position |
| 1:15–1:45 | Pause，等待 entry 與 exit 條件 | 新 Entry skipped；既有部位 Exit／SL／TP 可用 |
| 1:45–2:15 | Resume；模擬 stale／provider disconnect 後恢復 | 中斷期間沒有新 Entry，恢復後不補送舊訊號 |
| 2:15–3:00 | 有 pending order／open position 時重啟服務 | Runtime 進 Recovery Lock；對帳成功後仍需人工 ARM |
| 3:00–3:30 | 啟用 Kill Switch，並測試手動緊急平倉 | 新曝險為零；reduce-only close 可用且不反向 |
| 3:30–4:00 | Stop Runtime、核對帳本與 production guard | 不強制平倉；保護價管理至 flat；真實券商呼叫為零 |

若測試時段沒有自然產生指定策略訊號，可用同版本、固定 K 棒 fixture 在 staging/mock
重現；不可改用 browser 偽造 Decision、風控價格或成交。

## 必須通過的指標

| 指標 | 通過值 | 證據來源 |
|---|---:|---|
| duplicate order | 0 | orders 的 deterministic idempotency key／decision_id 重複檢查 |
| unmatched fill | 0 | fills 與 orders correlation 對帳 |
| inconsistent position | 0 | position ledger 與 fills 重建結果 |
| stale-market auto entry | 0 | stale/disconnected 時間窗內的 entry fills |
| real broker call | 0 | worker mode、broker audit／外部 client invocation counter |
| recovery failure | 0 | runtime recovery issue 與 paper recovery issue |

同時保存 `active_runtimes`、`armed_runtimes`、`decisions`、`executed_decisions`、
`skipped_decisions`、`duplicate_decisions_blocked`、`gap_risk_rejected`、
`recovery_locked_runtimes`、`last_runtime_decision_time` 與 `last_auto_order_time` 的
起訖快照。

## Evidence 紀錄格式

```text
PR / commit:
Environment / deployment run:
Operator:
Start / end (Asia/Taipei):
Runtime ID / strategy snapshot hash:
Health baseline artifact:
Health final artifact:
Orders / fills / positions reconciliation artifact:
Disconnect and restart timeline:
Manual actions (arm, pause, resume, stop, kill, emergency close):
Six required zero-count metrics:
Unexpected events and disposition:
Result: PASS / FAIL
Reviewer:
```

任何一個必要指標非零、對帳無法確認、Recovery Lock 被繞過，或發現外部券商 order API
呼叫，都必須判定 FAIL、維持 fail-closed，且不得以重新 ARM 取代根因調查。
