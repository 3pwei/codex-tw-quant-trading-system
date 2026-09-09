# 程式架構與依賴規則

本文件是新進維護者理解系統的入口。現階段平台提供行情、策略研究、歷史回測、
Replay 與 Paper Trading；真實券商下單尚未啟用。架構調整採漸進式遷移，既有 API
在替代實作完成前不得直接移除。

## 執行路徑

| 路徑 | 入口 | 執行方式 | 外部券商 |
|---|---|---|---|
| 歷史回測 | `tw_quant.backtest.runner` | 先產生策略訊號，再重建事件與模擬成交 | 不會連線 |
| 即時策略 | `GET /api/strategy-signals` | 重新分析目前 K 棒並回傳訊號 | 不會送單 |
| Replay | `/api/replay/sessions/*` | 隔離帳戶中的手動模擬市價單 | 不會連線 |
| Paper | `POST /api/paper/orders` | 帳戶風控後，以伺服器行情模擬成交 | 不會連線 |
| Shioaji Simulation | `ShioajiSimulationExecutionClient` | SDK 整合測試，尚未接 API／Worker | 模擬環境限定 |
| Live | `OrderExecutor` port | `DisabledBroker` fail closed | 停用 |

「即時策略訊號」和「Paper 委託」目前沒有自動串接。任何自動交易功能都必須透過
持久化的 Strategy Runner、帳戶風控與 Order Manager，不得由 API 查詢或前端直接
呼叫券商 adapter。

## 現有模組責任

| 模組 | 責任 | 不應負責 |
|---|---|---|
| `market_data` | 外部行情 Provider 與正規化 | 策略、風控、下單 |
| `market` | Tick、KBar、時段與週期 | 券商 SDK |
| `strategy` | 由標準 KBar 產生策略意圖與圖層 | 帳戶風控、券商送單 |
| `events` | 穩定事件契約與確定性事件迴圈 | 交易策略 |
| `risk` | 策略風險價格與帳戶風控決策 | 模擬成交、部位猜測 |
| `execution` | 訊號執行政策、模擬成交與 Position Ledger | HTTP、使用者介面 |
| `paper` | Paper use case、BrokerPort adapter、持久化與復原 | 真實券商送單 |
| `broker` | 訂單契約、生命週期、durable outbox、Broker port 與 adapter | 行情供應、策略規則 |
| `live` | API、WebSocket、組裝服務與監控 | 交易領域規則 |

## 依賴方向

核心事件與交易模型不得 import FastAPI、SQLite、Dashboard 或 Shioaji。外部 adapter
可以依賴核心 port，核心不能反向依賴 adapter。行情帳戶與交易帳戶維持分離。

```text
Interfaces (API / Worker)
  → Application services
    → Domain events and policies
      ← Ports
        ← Paper / SQLite / Shioaji adapters
```

新增 Live Execution 時沿用以下責任邊界：

1. Strategy Runner 只消費已收盤 K 棒並產生具冪等 ID 的 `SignalEvent`。
2. `LiveOrderManager` 先將核准委託與 outbox 原子寫入，再交給 `BrokerPort`。
3. Risk Gate 只能核准、縮減或拒絕，不得自行建立成交。
4. Broker Adapter 只轉換請求與回報，不包含策略規則。
5. Position Ledger 只根據 `FillEvent` 改變持倉。
6. Reconciliation Service 以券商委託、成交與部位為 Live 真實來源。

`broker` 內部依賴方向為：

```text
BrokerOrderRequest / BrokerOrderStatus
  → lifecycle policy
  → LiveOrderManager → SQLiteLiveOrderRepository
                     → BrokerPort → ShioajiBrokerAdapter → normalized SDK client
```

`ShioajiSimulationExecutionClient` 是獨立於行情 provider 的 simulation-only SDK
adapter。它把阻塞 SDK 呼叫移到 worker thread，集中正規化 Shioaji 委託狀態與成交
均價；callback edge 只驗證並 enqueue。`BrokerCallbackConsumer` 先把 callback 寫入
獨立 audit store，再透過 `LiveOrderManager` 查詢券商真實狀態；audit repository、
order repository 與 SDK adapter 都依賴 protocol，不互相反向 import。

callback 內容是喚醒 reconciliation 的提示，不是訂單與部位的真實來源。consumer
只能 refresh 已有 `broker_order_id` 的本地委託；找不到配對時留下 audit 狀態，不得
推測 client order ID 或送出替代委託。

`ShioajiBrokerAdapter` 不得直接由 HTTP handler 建立。正式上線前仍需背景 Worker、
callback consumer 的 production 組裝、完整 orders/fills/positions 對帳與營運解鎖
流程。Production client 必須是明確的新組裝路徑，不得讓 simulation client 接受
`simulation=False`。

Paper HTTP handler 使用 `BrokerOrderRequest` 呼叫 `PaperTradingService.submit_request()`；
service 保留帳戶風控與事件持久化責任，`PaperBrokerAdapter` 則提供相同的 `BrokerPort`
給非 HTTP 呼叫端。相容用的 `PaperOrderCommand` 在 Replay 完成遷移前不得移除。

## 相容與淘汰原則

- `linear_channel_breakout` 現為 Dow Theory 策略的相容 key；對外遷移完成前保留。
- `tw_quant.engine.BacktestEngine` 是較早期的日內股票回測路徑；新 TMF 功能不得再
  增加對它的依賴。
- `execution.simulator` 負責策略層的 next-open 研究語意；
  `execution.event_simulator` 負責事件、成交成本與持倉。不得再新增第三套成交核心。
- `execution.position_ledger` 是持倉與已實現交易的唯一帳本；只接受 `FillEvent`，
  不得依賴 API、資料庫或券商 adapter。`event_simulator` 暫時 re-export 舊名稱以維持
  import 相容。
- `execution.signal_router` 將策略訊號轉成 position-aware `OrderIntent`；
  `risk_gates` 只決定核准與否；`simulated_broker` 只處理確定性模擬成交；
  `liquidator` 只建立時段與換月減倉意圖；`pipeline` 是唯一組裝位置。
- `execution.event_simulator` 已收斂為相容 facade，新程式不得再把實作加入該檔案。
- 移除舊介面前，必須先建立相容 adapter、遷移測試與 ADR。

## Pull Request 驗收

交易相關 PR 必須同時滿足：

- 公開函式與事件具明確型別，拒絕原因使用集中定義的穩定代碼。
- Domain／policy 不依賴 framework 或外部 SDK。
- Paper 與未來 Live adapter 通過相同 contract tests。
- 新設定集中於 config，不在 handler 或 UI 散落魔法數字。
- 行為變更包含單元測試、事件整合測試與失敗路徑。
- 更新本文件、`order-lifecycle.md` 或對應操作手冊。
