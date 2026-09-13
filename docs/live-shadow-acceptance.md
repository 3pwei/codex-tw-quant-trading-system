# Live Shadow 驗收程序

Live Shadow 不是 Live Trading。它使用真實市場 BidAsk、read-only broker truth、Recovery
與 server-side Live Risk 推演 canonical request，但 production composition 沒有 live sink。
全程必須維持 `LIVE_TRADING_ENABLED=false`。

## 前置核對

1. 完成並保存一個日盤及一個夜盤的 read-only recovery soak。
2. 確認 execution worker 為 `READY_READ_ONLY` 且 `ordering_enabled=false`。
3. 由 server 產生的 opaque `target_id` 指派給 owner；`market.env` 不得含完整 broker
   account ID、CA 或 execution credential。
4. 設定 canonical contract allowlist、expiry、tick size、multiplier、session 與所有 Live
   limits，雙人覆核 policy hash/version。
5. 先以 `LIVE_SHADOW_ENABLED=false` 啟動並驗證 Paper／Research，再明確啟用 Shadow。

## 日盤與夜盤

每個 session 留存：部署 commit、risk/policy version、broker masked account、decision
總數、would-submit、reject reasons、quote age、spread、evaluation latency、reservation、
paper/shadow action 與 direction match/mismatch。抽查每個 would-submit 的 strategy snapshot、
protective stop、broker position、owner 跨券商曝險及 target capability。

價格不要求與 Paper 相同：Paper 依 next-bar-open simulation，Shadow 依當下可執行 BidAsk。
比較只針對相同 source bar 的 action 與 direction。

## Fault injection

依序模擬 Recovery locked/reconciling、broker disconnected、stale/missing BidAsk、stale/missing
position truth、missing risk config、capability mismatch、policy exception、duplicate decision、
兩個 Runtime 同時 entry，以及相同 account ID 的兩個 broker。每項必須拒絕或只產生單一
reservation；不得 fallback target、建立 live outbox 或呼叫 broker。

## Acceptance targets

| 指標 | 目標 |
|---|---:|
| Real submit | 0 |
| Real cancel / replace | 0 |
| Live outbox created by Shadow | 0 |
| Risk bypass | 0 |
| Duplicate shadow evaluation | 0 |
| Stale quote would-submit | 0 |
| Recovery-locked would-submit | 0 |
| Disallowed contract would-submit | 0 |
| Portfolio limit bypass | 0 |
| Secret/full account leak | 0 |

任一項不為零即停用 `LIVE_SHADOW_ENABLED`、保留 SQLite audit 與 health evidence，且不得
以啟用 real execution 作為除錯手段。
