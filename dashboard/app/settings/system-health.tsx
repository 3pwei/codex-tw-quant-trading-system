"use client";

import { useCallback, useEffect, useState } from "react";

type PaperHealth = {
  status: "healthy" | "degraded";
  restored_orders: number;
  restored_fills: number;
  restored_positions: number;
  recovery_duration_ms: number;
  inconsistent_owners: number;
  recovery_issue_count: number;
  submission_requests: number;
  filled_submissions: number;
  rejected_submissions: number;
  market_blocked_requests: number;
  active_kill_switches: number;
  manual_kill_switches: number;
  automatic_kill_switches: number;
  average_submission_ms: number | null;
  max_submission_ms: number;
  repository: {
    average_write_ms: number | null;
    max_write_ms: number;
  };
};
type HostHealth = {
  cpu_percent: number | null;
  cpu_count: number | null;
  memory_used_bytes: number;
  memory_total_bytes: number;
  memory_percent: number | null;
  disk_used_bytes: number;
  disk_total_bytes: number;
  disk_percent: number | null;
};
type SystemHealth = {
  system_status: "healthy" | "degraded" | "market_stale" | "provider_disconnected" | "trading_halted";
  service_status: "healthy" | "degraded" | "market_stale" | "provider_disconnected";
  connection_status: string;
  last_tick_time: string | null;
  last_bar_time: string | null;
  market_latency_seconds: number | null;
  tick_age_ms: number | null;
  queue_size: number;
  queue_capacity: number;
  queue_high_watermark: number;
  dropped_ticks: number;
  processed_ticks: number;
  worker_errors: number;
  average_tick_processing_ms: number | null;
  max_tick_processing_ms: number;
  average_database_write_ms: number | null;
  max_database_write_ms: number;
  websocket_connections: number;
  websocket_connections_total: number;
  websocket_disconnections_total: number;
  websocket_dropped_messages: number;
  paper_trading: PaperHealth;
  host: HostHealth;
};

const numeric = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 3 });
const value = (item: number | null, suffix = "") => item === null
  ? "—"
  : `${numeric.format(item)}${suffix}`;
const bytes = (item: number) => `${numeric.format(item / 1024 / 1024 / 1024)} GB`;
const clock = (item: string | null) => item
  ? new Date(item).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false })
  : "尚未收到";
const statusLabel: Record<SystemHealth["system_status"], string> = {
  healthy: "HEALTHY",
  degraded: "DEGRADED",
  market_stale: "MARKET STALE",
  provider_disconnected: "PROVIDER OFFLINE",
  trading_halted: "TRADING HALTED",
};

export default function SystemHealthPanel() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/admin/health", { cache: "no-store" });
      if (!response.ok) throw new Error(`健康檢查失敗 (${response.status})`);
      setHealth(await response.json());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法讀取系統健康狀態");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const timer = window.setInterval(() => void load(), 10_000);
    return () => { window.clearTimeout(initial); window.clearInterval(timer); };
  }, [load]);

  return <section className="system-health panel">
    <div className="panel-head">
      <div><span>OPERATIONS</span><h2>系統健康監控</h2></div>
      <strong className={health?.system_status === "healthy" ? "profit" : "loss"}>
        {!health ? "讀取中" : statusLabel[health.system_status]}
      </strong>
    </div>
    {error && <p className="system-health-error">{error}</p>}
    {health && <>
      <div className="system-health-grid">
        <article><span>最新 Tick／K 棒</span><b>{clock(health.last_tick_time)}</b><small>K 棒 {clock(health.last_bar_time)}</small></article>
        <article><span>行情連線</span><b>{health.connection_status}</b><small>Tick age {value(health.tick_age_ms, " ms")}</small></article>
        <article><span>行情傳輸延遲</span><b>{value(health.market_latency_seconds, " 秒")}</b><small>Provider → API</small></article>
        <article><span>WebSocket</span><b>{health.websocket_connections} 條連線</b><small>累計 {health.websocket_connections_total} · 斷線 {health.websocket_disconnections_total} · 丟棄 {health.websocket_dropped_messages}</small></article>
        <article><span>行情 Queue</span><b>{health.queue_size} / {health.queue_capacity}</b><small>最高 {health.queue_high_watermark}</small></article>
        <article><span>Tick 處理</span><b>{value(health.average_tick_processing_ms, " ms")}</b><small>最大 {value(health.max_tick_processing_ms, " ms")}</small></article>
        <article><span>行情資料庫寫入</span><b>{value(health.average_database_write_ms, " ms")}</b><small>最大 {value(health.max_database_write_ms, " ms")}</small></article>
        <article><span>行情錯誤</span><b>{health.dropped_ticks + health.worker_errors}</b><small>丟棄 {health.dropped_ticks} · Worker {health.worker_errors}</small></article>
        <article><span>Paper 復原</span><b>{health.paper_trading.status}</b><small>持倉 {health.paper_trading.restored_positions} · {value(health.paper_trading.recovery_duration_ms, " ms")}</small></article>
        <article><span>Paper 一致性</span><b>{health.paper_trading.recovery_issue_count}</b><small>受影響帳戶 {health.paper_trading.inconsistent_owners}</small></article>
        <article><span>Paper 委託／成交／拒絕</span><b>{health.paper_trading.submission_requests} / {health.paper_trading.filled_submissions} / {health.paper_trading.rejected_submissions}</b><small>行情阻擋 {health.paper_trading.market_blocked_requests}</small></article>
        <article><span>委託處理</span><b>{value(health.paper_trading.average_submission_ms, " ms")}</b><small>最大 {value(health.paper_trading.max_submission_ms, " ms")}</small></article>
        <article><span>Paper 資料庫寫入</span><b>{value(health.paper_trading.repository.average_write_ms, " ms")}</b><small>最大 {value(health.paper_trading.repository.max_write_ms, " ms")}</small></article>
        <article><span>Kill Switch</span><b>{health.paper_trading.active_kill_switches ? `${health.paper_trading.active_kill_switches} 個啟用` : "全部解除"}</b><small>手動 {health.paper_trading.manual_kill_switches} · 自動 {health.paper_trading.automatic_kill_switches}</small></article>
        <article><span>CPU</span><b>{value(health.host.cpu_percent, "%")}</b><small>{health.host.cpu_count ?? "—"} vCPU</small></article>
        <article><span>記憶體</span><b>{value(health.host.memory_percent, "%")}</b><small>{bytes(health.host.memory_used_bytes)} / {bytes(health.host.memory_total_bytes)}</small></article>
        <article><span>磁碟</span><b>{value(health.host.disk_percent, "%")}</b><small>{bytes(health.host.disk_used_bytes)} / {bytes(health.host.disk_total_bytes)}</small></article>
      </div>
      <p className="system-health-note">每 10 秒更新；此頁只顯示彙總，不會揭露其他使用者的策略、委託或持倉。</p>
    </>}
  </section>;
}
