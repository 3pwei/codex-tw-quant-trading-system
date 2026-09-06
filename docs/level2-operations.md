# Level 2 正式運行與驗收

本文件定義事件式 Backtest／Replay／Paper Trading 完成後的正式運行邊界。平台仍只
提供模擬成交，不連接外部券商下單 API。

## 重啟復原

`PaperTradingService` 啟動時依 SQLite 的 durable write order 重播 Fill 與控制紀錄，
重新建立：

- 使用者隔離的多空持倉、均價、成本與已實現損益。
- 當日成交次數、連敗、冷卻與自動風控狀態。
- 手動 Kill Switch 的最後啟用／解除狀態。
- 既有 `Idempotency-Key` 對應，重送不會建立第二筆委託。

啟動後會核對 Order → Risk → Fill → Position 事件鏈、最新 Position 快照，以及是否
存在只有冪等保留、沒有 Order Intent 的中斷寫入。任何帳戶不一致時：

1. 該帳戶標示為 `recovery_status=degraded`。
2. 自動啟用原因為 `recovery_inconsistent` 的 Kill Switch。
3. 禁止新增曝險，但保留 reduce-only 平倉能力。
4. 問題代碼只回傳給資料擁有者；管理員健康頁只顯示彙總數量。

## 監控

管理員可從 `/settings/` 查看每 10 秒更新的彙總，也可呼叫
`GET /api/admin/health`。重要欄位：

| 區域 | 指標 | 判讀 |
|---|---|---|
| 行情 | `service_status` | `healthy` 以外需要檢查 |
| Provider | `connection_status`、`tick_age_ms` | 交易時段內斷線或 Tick 持續老化需處理 |
| Queue | size、capacity、high watermark | 持續上升代表消費速度不足 |
| Worker | dropped ticks、errors | 任一增加都會將服務標示 degraded |
| 效能 | Tick average/max processing ms | 用來觀察版本間退化，不是成交延遲承諾 |
| Paper | recovery status/issues | degraded 帳戶已自動禁止新增曝險 |
| Paper | submission average/max ms | 只統計本次程序啟動後的 API 處理 |

Worker 遇到單筆壞資料或 listener 例外時會記錄錯誤並繼續處理 Queue，不會讓唯一的
行情 Worker 靜默終止。

## 部署健康檢查

部署前會執行 Python 完整測試、Dashboard lint/build，並建立 SQLite Online Backup；
備份完成後必須通過 `PRAGMA integrity_check` 才能繼續切換服務。
服務切換後 Market API 容器必須為 healthy；公開 `${PUBLIC_DASHBOARD_URL}/healthz`
必須在跟隨轉址後得到本文完全等於 `ok` 的 HTTP 200 回應。

Cloudflare Zero Trust 必須為 `/healthz` 建立比 Dashboard 更精確的 Bypass Application。
若仍回到 Access 登入頁，部署的最後驗證會失敗，不能再把 302 誤判為成功。

## 正式驗收清單

- [ ] Trader／明確啟用 Paper 的 Admin 可送模擬單；Researcher 無法進入。
- [ ] 不同使用者互相看不到委託、成交、持倉與問題代碼。
- [ ] 重複點擊或網路重送相同 Idempotency-Key 只產生一筆委託。
- [ ] 行情超過兩分鐘時，新委託 fail closed。
- [ ] 手動 Kill Switch 阻止新倉但允許平倉，重啟後仍保持啟用。
- [ ] 有持倉時重啟 Market API，持倉、損益與當日交易次數保持一致。
- [ ] 故意建立中斷冪等紀錄後重啟，帳戶進入 recovery lock。
- [ ] listener 單筆失敗後 Worker 繼續運行，錯誤計數增加。
- [ ] 200 筆連續 Paper API 測試符合 CI 寬鬆預算：平均小於 100 ms、最大小於 1 秒。
- [ ] 部署前 SQLite 備份存在且可由 SQLite 開啟。
- [ ] CI、部署 verify、容器 health 與公開 healthz 全部成功。

測試門檻是避免明顯效能退化，不代表外部券商 SLA，也不應用於 HFT 延遲宣稱。
