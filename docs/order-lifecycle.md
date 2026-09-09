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

策略模擬政策目前預設每個分析群組最多一個進場。日內群組是合約、時段與交易日；
日 K／週 K 群組目前只有合約。這是明確的研究政策，不是 Paper 帳戶限制。

### Paper 與 Replay

```text
Manual request → OrderIntent → AccountRiskGate
               → immediate simulated full FillEvent → PositionEvent
```

目前僅支援市價、立即、全數成交。`stop_loss_price` 只供進場風險核准及圖表顯示，
不是會在後續行情觸發的保護委託；目前也沒有 Paper `take_profit_price`。在真正的
Protective Order／OCO 完成前，任何 UI 或文件都不得宣稱 Paper 部位已有自動保護。

### Live

Live execution foundation 已包含型別化 `BrokerPort`、不可變訂單狀態、SQLite
order/outbox、`LiveOrderManager` 與 `ShioajiBrokerAdapter` 的 SDK seam。這些元件尚未接入
API 或背景 Worker，也沒有真實 Shioaji client，因此正式站仍由 `DisabledBroker`
拒絕所有外部送單。

送單前，`LiveOrderManager.create()` 會在同一個 transaction 保存 order 與 outbox；
Worker 只能領取 `pending` 工作一次。送單逾時、程序中斷或結果不明會轉成 `UNKNOWN`
並將 outbox 設為 `blocked`，必須先透過券商查詢完成 reconciliation，不得自動重送。

## 目標生命週期

```text
CREATED → RISK_APPROVED → SUBMITTING → ACCEPTED
        → PARTIALLY_FILLED → FILLED
        → CANCEL_PENDING → CANCELLED
        → REJECTED / EXPIRED / UNKNOWN
```

`UNKNOWN` 表示請求結果不明。系統必須先向券商查詢，不得自動重送可能已被接受的
委託。

目前 Paper API 保留舊的 `status` 以維持相容，並額外回傳 `lifecycle_status`：

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
8. 本地與券商持倉不一致時 fail closed，留下稽核紀錄並要求人工處理。
9. 啟用 Shioaji adapter 必須同時滿足 provider、enable flag、確認字串與帳號 allowlist；
   任一缺失都維持 fail closed。
