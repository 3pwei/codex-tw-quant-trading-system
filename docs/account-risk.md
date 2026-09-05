# Paper Trading 帳戶風控

`AccountRiskGate` 是事件式模擬成交管線的 fail-closed 風控入口。目前只允許 Paper
Trading，不支援也不應用於真實券商下單。

## 核准順序

每個新曝險訂單依序檢查：

1. `order_id` 尚未處理過。
2. `app_users` 權限快照存在，帳號狀態為 `active`。
3. `trading_mode=paper` 且包含 `orders.paper`。
4. 手動或自動 Kill Switch 未啟用。
5. 不在連敗冷卻期間。
6. 未達每日最大虧損與最大交易次數。
7. 現有部位加待成交保留量未超過最大口數。
8. 訂單包含方向正確的停損價。
9. 停損價差、契約乘數、雙邊成本與滑價估算的單筆風險未超限。

任一步驟失敗都產生拒絕的 `RiskDecision`，不會產生 `FillEvent`。決策同時寫入
記憶體 `audit_log`；後續 Paper Session 持久化階段會將相同資料寫入 SQLite。

## 預設限制

| 規則 | 預設值 |
|---|---:|
| 帳戶最大未平倉口數 | 2 口 |
| 單筆最大估計風險 | NT$5,000 |
| 每交易日最大虧損 | NT$10,000 |
| 每交易日最大進場次數 | 10 次 |
| 連續虧損觸發門檻 | 3 次 |
| 連敗冷卻 | 30 分鐘 |

上述是工程安全預設值，不是投資建議。Paper Session 建立時應保存完整設定快照，
避免執行中修改全域參數而改變既有 Session 行為。

## Kill Switch

- 每日已實現淨損益低於限制時自動啟用，下一交易日自動解除。
- 手動 Kill Switch 跨交易日維持，必須明確執行 reset。
- 啟用與解除必須提供含時區時間及原因，並寫入 `control_log`。
- Kill Switch 只阻止新增曝險；合法的 `reduce_only` 與強制平倉仍可核准。

## 交易日與未成交單

風控優先使用事件攜帶的 TMF `trading_date`，因此 15:00 開始、跨午夜的夜盤仍算
同一交易日。交易時段進入 `closing` 或 `closed` 時，模擬券商會取消尚未成交的
next-open 訂單，風控同步釋放保留口數。

## 權限資料來源

`TradingAccessRegistry` 接收 `AuthUser` 快照，欄位直接對應既有 `app_users`：

- `user_id` → owner ID
- `status` → 是否 active
- `trading_mode` → 必須為 paper
- `permissions` → 必須包含 `orders.paper`

後續 Paper Trading API 必須從伺服器端的登入使用者建立快照，不接受瀏覽器自行
提交 owner、role、mode 或 permissions。
