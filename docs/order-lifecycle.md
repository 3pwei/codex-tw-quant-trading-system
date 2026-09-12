# 訂單生命週期

本文件區分研究訊號、Paper 委託與未來 Live 委託，避免將 signal、order 與 fill
混用為「下單」。

## 共用名詞

| 名詞 | 定義 |
|---|---|
| `SignalEvent` | 策略判斷結果，尚未通過帳戶風控 |
| `OrderIntent` | 系統希望執行的委託 |
| `RiskDecision` | 核准、縮減或拒絕結果 |
| `BrokerOrder` | Paper 或券商接受處理的委託 |
| `FillEvent` | 全部或部分成交事實 |
| `PositionEvent` | Fill 套用至帳本後的持倉快照 |
| `RealizedTrade` | 反向 Fill 結束全部或部分部位後的交易紀錄 |

部位只能由 `FillEvent` 改變。HTTP 成功、券商接受或 Risk 核准都不代表已成交。

## 現行生命週期

### 歷史回測

```text
Closed K → strategy intent → next-open / risk exit price
         → SignalEvent → OrderIntent → ResearchRiskGate
         → simulated full FillEvent → PositionEvent
```

策略模擬不限制每個分析群組或交易日的進場次數；平倉後出現新的有效 entry intent
即可再次進場。連續維持同方向的條件只算一個 intent，必須先回到 neutral，才會為
同方向重新武裝，避免停損後每根 K 棒反覆進場。Paper／Live 的帳戶每日額度仍由
`AccountRiskGate` 獨立控制，不套用至歷史回測。

### Paper 與 Replay

```text
Manual request → BrokerOrderRequest → OrderIntent → AccountRiskGate
               → immediate simulated full FillEvent → PositionEvent
```

Paper API 現在使用與未來 Live 相同的型別化 `BrokerOrderRequest` 作為 application
boundary，再由 `PaperTradingService` 轉為現有事件鏈。`PaperBrokerAdapter` 實作
`BrokerPort`，讓背景 runner 或 contract test 可以使用同一套 broker 介面；舊的
`PaperOrderCommand` 暫時保留給 Replay 與相容呼叫端。

armed 的 `paper_auto` Runtime 使用 managed position 路徑：

```text
Closed K → durable Entry Decision → Paper Auto safety gates
         → AccountRiskGate → next_bar_open OrderIntent
         → next closed bar open FillEvent → protected PositionEvent
Closed K → SL / TP（stop first）→ bar_trigger reduce-only FillEvent
         → durable Strategy Exit Decision → next-open reduce-only FillEvent
```

自動單的 idempotency key 由 owner、runtime、contract、trigger time、direction 與 entry
動作決定；retry 不會增加委託。Runtime quantity 與 `stop_loss_pct` 都取自 immutable
snapshot，reference price 取訊號確認 K 的 close。Order、Fill、Position 會保存
`order_source=strategy_auto`、runtime 與 decision attribution。manual Paper 仍使用
`current_close`，不受此時序改動影響。

自動 entry 的 planned stop 只供 AccountRiskGate 核准。成交後會改以 actual fill 與
immutable strategy snapshot 重算並保存真正的 stop loss／take profit；reference close
與 next open 的跳空仍保留在 reference、planned stop 與 actual fill audit 欄位。

next-open entry 在 Fill 前會再經一次 AccountRiskGate。若 executable open 已越過
planned stop，或重新估算的單筆風險超限，Order 會在 Fill 前以
`gap_risk_exceeded` 終止。重啟時先前 armed 的 Runtime 進入
`recovery_locked`；已核准但未成交的舊 entry 會標記
`stale_or_recovered_signal`，不會在重新連線後補送。

手動 Paper 仍僅支援市價、立即、全數成交，手動輸入的 `stop_loss_price` 只供進場
風險核准及圖表顯示。Paper Auto managed exit 是平台內的 closed-bar simulated policy，
不是外部券商原生 Protective Order／OCO；服務中斷期間不會在券商端獨立保護部位。

### Shioaji Simulation 與 Live

Live execution foundation 已包含型別化 `BrokerPort`、不可變訂單狀態、SQLite
order/outbox、`LiveOrderManager`、`ShioajiBrokerAdapter` 與 simulation-only SDK client。
SDK client 只會以 `Shioaji(simulation=True)` 登入，支援期貨市價／限價送單、撤單、
委託查詢與部位快照；不載入 CA，也沒有 production 建構路徑。

這些元件尚未接入 API 或背景 Worker，因此正式站仍由 `DisabledBroker` 拒絕所有外部
送單。Simulation client 是整合測試邊界，不代表正式站已啟用 Paper 或 Live 自動下單。

送單前，`LiveOrderManager.create()` 會在同一個 transaction 保存 order 與 outbox；
Worker 只能領取 `pending` 工作一次。送單逾時、程序中斷或結果不明會轉成 `UNKNOWN`
並將 outbox 設為 `blocked`，必須先透過券商查詢完成 reconciliation，不得自動重送。
`LiveOrderManager.reconcile_broker_orders()` 只查詢可能已到達券商的委託；尚在 outbox
等待、狀態為 `risk_approved` 的訂單不會被誤判為券商遺失。callback bridge
只把 `FORDER`／`FDEAL` 正規化並放進記憶體 queue，不直接寫資料庫。callback consumer
先以內容雜湊的 `event_id` 將事件寫入 `live_broker_events`，再以 `broker_order_id` 查找
本地委託並向券商 refresh；它不會直接相信 callback 內的狀態或成交價格。重複 callback
只會完成一次成功對帳，較早到達、尚未配對的 callback 仍可在委託落庫後重試。

queue 滿載、callback 漏失或程序中斷時，週期性 reconciliation 仍是復原來源。找不到
本地委託、缺少券商委託 ID 或 refresh 失敗都會留下 `unmatched`／`failed` audit 狀態，
不會建立或補送委託。

### Recovery Lock 與三方對帳

Live execution 啟動時預設為 `locked`，不得建立新委託，也不得 dispatch 已在 outbox
等待的委託。`LiveReconciliationService` 先 refresh 本地非終態訂單，再一次擷取券商
orders、deals 與 positions，依序檢查：

1. broker 與 account identity 必須完全相同。
2. 每筆券商委託必須有唯一 `broker_order_id` 並能對應本地訂單。
3. 本地與券商的訂單狀態、累計成交量必須一致。
4. deal 不得重複或成為 orphan，deal 數量總和必須等於券商委託累計成交量。
5. 由本地累計成交推導的各契約淨部位必須等於券商 positions。

全部一致才把狀態改為 `ready`。任何 mismatch、snapshot 失敗或 `UNKNOWN` 無券商 ID
都維持 `locked`，並保存穩定 issue code。程序若在對帳途中停止，資料庫會保留
`reconciling`，下次啟動仍視為未解鎖。

目前 Recovery Lock、對帳 service 與 Shioaji simulation snapshot 已具備可測試的組裝
邊界，但正式站尚未啟動 execution worker，因此不會進行外部送單。

Shioaji 期貨委託目前沒有採用已驗證、可持久化的 client order ID 欄位。因此若送單
逾時且尚未取得 `broker_order_id`，系統會保持 `UNKNOWN` 並要求人工核對，不會以價格、
時間或數量猜測同一筆委託，更不會自動補送。

## 目標生命週期

```text
CREATED → RISK_APPROVED → SUBMITTING → ACCEPTED
        → PARTIALLY_FILLED → FILLED
        → CANCEL_PENDING → CANCELLED
        → REJECTED / EXPIRED / UNKNOWN
```

`UNKNOWN` 表示請求結果不明。系統必須先向券商查詢，不得自動重送可能已被接受的
委託。

Paper API 保留舊的 `status` 以維持相容，並額外回傳 `lifecycle_status`：

| Paper status | Canonical lifecycle status |
|---|---|
| `pending_risk` | `created` |
| `approved` | `risk_approved` |
| `rejected` | `rejected` |
| `filled` | `filled` |

每筆 Live 委託至少保存：

- 平台 `client_order_id` 與券商 `broker_order_id`
- owner、strategy ID／version、symbol 與實際契約
- side、quantity、order type、價格與 reduce-only
- Paper／Live mode
- requested、approved、submitted、accepted、filled、cancelled 時間
- 累計成交量、成交均價、手續費、稅與拒絕原因
- signal、order、risk、fill 的 correlation／causation IDs

## Live 安全規則

1. Paper 與 Live 的帳戶、Ledger、權限、ID 空間及 Kill Switch 必須隔離。
2. 送單前先持久化 Order 與 outbox；Worker 成功取得工作後才呼叫券商。
3. 相同 signal／client order ID 最多建立一個外部委託。
4. 停損優先使用券商端原生保護；平台合成停損必須清楚標示中斷風險。
5. 行情過期禁止新增曝險，但不得阻止透過券商通道執行緊急減倉。
6. Kill Switch 分為禁止進場、全部撤單、全部平倉，不得共用模糊布林值。
7. 重啟後先和券商核對 orders、fills、positions，完成前禁止自動進場。
8. Paper Auto 必須由 `source_bar_id → decision_id → order → risk decision → fill → position`
   的 correlation／causation metadata 完整追溯。
8. 本地與券商持倉不一致時 fail closed，留下稽核紀錄並要求人工處理。
9. 啟用 Shioaji adapter 必須同時滿足 provider、enable flag、確認字串與帳號 allowlist；
   任一缺失都維持 fail closed。
10. Simulation 與 Production 使用不同 client 組裝路徑；未來 production client 必須
    另外完成 CA、交易帳號、權限、Kill Switch 與操作人員解鎖，不得替換 simulation
    旗標後直接沿用。

## 尚未完成的實盤能力

- 持久化 Strategy Runner、帳戶 Risk Gate 與 LiveOrderManager Worker 的正式串接。
- callback consumer 的正式 Worker 組裝、audit retention 與 callback 漏失監控。
- 三方對帳的正式排程、監控告警與人工 mismatch 處理介面。
- 本地 Position Ledger 與券商部位差異的自動停機及人工解除流程。
- 原生保護委託／OCO、部分成交後保護數量調整，以及撤單競態處理。
- Shioaji production client、CA 憑證生命週期、金鑰輪替與真實帳號 allowlist。
- 每日額度、單筆額度、最大曝險、行情新鮮度、交易時段與全域 Kill Switch。
- UNKNOWN 無 broker order ID 的營運對帳介面；完成前不得自動重送。
