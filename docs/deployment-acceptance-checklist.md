# 部署驗收清單

每次正式部署保存此清單的日期、操作者、PR、commit、備份檔名與 GitHub Actions URL。

## 部署前

- [ ] PR 已通過 Python 3.10、Python 3.12、Dashboard、Lightsail Images。
- [ ] 變更已審查，部署 commit 是 `master` 的 ancestor。
- [ ] 已確認 Lightsail CPU、記憶體、磁碟有足夠空間。
- [ ] 已避開重要持倉操作時段，並通知測試帳號。
- [ ] 自動備份檔名與預期 commit 一致，SQLite integrity check 為 `ok`。

## 自動部署

- [ ] Deploy verify 的完整 Python 測試通過。
- [ ] Dashboard `npm ci`、lint、build 通過。
- [ ] `market-api` 為 healthy，gateway 為 running。
- [ ] 公開 `/healthz` 跟隨轉址後仍是 HTTP 200，本文完全等於 `ok`。
- [ ] Cloudflare Access 登入頁未被誤判為健康回應。

## 部署後功能

- [ ] `/settings/` 顯示 CPU／記憶體／磁碟可讀。
- [ ] Provider connected；最新 Tick／K 棒時間與延遲合理。
- [ ] Queue 不持續累積，dropped ticks、worker errors 沒有增加。
- [ ] WebSocket 桌機與手機各連線一次，切換 5G／Wi-Fi 後能恢復且週期不亂跳。
- [ ] Trader 可送 Paper 單；Researcher 被拒絕。
- [ ] 快速重複點擊相同請求只產生一筆委託與成交。
- [ ] 兩個 Trader 的委託、成交、持倉互相隔離。
- [ ] Kill Switch 阻止新倉，最新報價有效時允許 reduce-only 平倉。
- [ ] Provider 中斷時禁止新倉，恢復後沒有補送委託。
- [ ] Paper Recovery 為 healthy，重啟前後持倉與風控狀態一致。
- [ ] `/trade/` 只顯示 Observe／Manual Paper／Paper Auto，沒有可操作的 Live 按鈕。
- [ ] Paper Auto ARM 需再次確認；Pause 阻止 Entry 但 managed exit 可用。
- [ ] 已依 [四小時 Paper Auto 驗收](automated-paper-trading-acceptance.md) 留存 metrics evidence。

## 驗收紀錄

| 欄位 | 紀錄 |
|---|---|
| 日期／操作者 | |
| PR／commit | |
| GitHub CI／Deploy URL | |
| SQLite 備份檔 | |
| 開始／結束時間 | |
| 異常與處置 | |
| 結論（通過／不通過） | |
