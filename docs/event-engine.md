# 統一事件引擎契約

此模組是 Backtest、Replay 與 Paper Trading 共用的執行骨幹。事件契約與確定性
迴圈之上已加入記憶體內模擬券商及部位帳本；不連接真實券商，也不改變既有 API。

## 事件流程

標準流程如下；風控拒絕時停在 `RiskDecision`，不會產生 `FillEvent`。

```text
MarketEvent / BarClosedEvent
  → SignalEvent
  → OrderIntent
  → RiskDecision
  → FillEvent
  → PositionEvent
```

`SessionEvent` 負責交易時段開啟、準備關閉與完成關閉。後續 Paper Trading 會用
`closing` 觸發強制平倉，以 `closed` 阻止新訂單。

## 模擬成交規則

- 策略 entry／exit 訊號轉成 market `OrderIntent`，成交量預設一口且可設定。
- 一般訂單只會在嚴格晚於訊號時間的下一根同契約 K 棒開盤成交。
- 買進滑價向上、賣出滑價向下；每邊手續費與期貨交易稅皆寫入 `FillEvent`。
- 部位以 owner、策略 ID／版本、symbol、contract 分帳，支援多空、加減碼與反手。
- 已實現損益扣除進出雙邊成本；未實現損益扣除尚未結轉的進場成本。
- `closing` 與換月事件以舊契約最後已知收盤價強制平倉，仍套用滑價與成本。
- 重複 Fill ID 不會再次改變部位。

`PassThroughRiskGate` 僅是帳戶風控完成前的模擬預設值；它不能將核准數量放大，
且可透過 `RiskGate` 介面替換。正式 Paper Trading 不得使用此預設閘門。

## 確定性規則

1. 事件必須使用含時區的 `occurred_at`。
2. 佇列先依事件時間排序；相同時間依入列順序處理。
3. 同一類事件的 handler 依註冊順序執行。
4. handler 不得產生早於原因事件的下游事件。
5. 相同 `event_id` 在同一引擎生命週期內最多處理一次。
6. `max_events` 會中止意外形成的無限事件鏈。

事件 ID 應以資料來源可重現的 key 產生。例如已收盤 1 分 K 可使用
`contract + timeframe + bar time`；券商成交回報應使用券商 fill ID。重新啟動或
重播相同來源資料時會得到相同 ID，讓重複投遞可以安全忽略。

## 追蹤欄位

- `event_id`：單一事件的唯一識別。
- `causation_id`：直接導致此事件的上一個事件。
- `correlation_id`：同一筆決策鏈共用的識別。
- `owner_id`：資料擁有者；Paper Trading 必須填入。
- `source`：事件的產生模組或外部來源。

`event_to_dict()` 提供 JSON 相容的 audit payload。後續持久化只儲存此穩定格式，
不直接 pickle Python 物件。

## 遷移順序

- 模擬成交與部位引擎已消費 `OrderIntent`／`RiskDecision`。
- 下一階段由帳戶層風控取代預設閘門，並記錄核准或拒絕原因。
- Backtest 與 Replay 最後改由相同事件流驅動。
- Paper Trading 只接已收盤 K 棒，並保存事件與狀態快照以支援重啟。

任何真實券商 adapter 都不在目前 Level 2 範圍內；`OrderExecutor` 預設仍維持停用。
