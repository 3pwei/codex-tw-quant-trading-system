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
  average_submission_ms: number | null;
  max_submission_ms: number;
};
type SystemHealth = {
  service_status: "healthy" | "degraded" | "provider_disconnected";
  connection_status: string;
  tick_age_ms: number | null;
  queue_size: number;
  queue_capacity: number;
  queue_high_watermark: number;
  dropped_ticks: number;
  processed_ticks: number;
  worker_errors: number;
  average_tick_processing_ms: number | null;
  max_tick_processing_ms: number;
  paper_trading: PaperHealth;
};

const numeric = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 3 });
const value = (item: number | null, suffix = "") => item === null
  ? "—"
  : `${numeric.format(item)}${suffix}`;

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
      <strong className={health?.service_status === "healthy" && health.paper_trading.status === "healthy" ? "profit" : "loss"}>
        {!health ? "讀取中" : health.service_status === "healthy" && health.paper_trading.status === "healthy" ? "HEALTHY" : "ATTENTION"}
      </strong>
    </div>
    {error && <p className="system-health-error">{error}</p>}
    {health && <>
      <div className="system-health-grid">
        <article><span>行情連線</span><b>{health.connection_status}</b><small>Tick age {value(health.tick_age_ms, " ms")}</small></article>
        <article><span>行情 Queue</span><b>{health.queue_size} / {health.queue_capacity}</b><small>最高 {health.queue_high_watermark}</small></article>
        <article><span>Tick 處理</span><b>{value(health.average_tick_processing_ms, " ms")}</b><small>最大 {value(health.max_tick_processing_ms, " ms")}</small></article>
        <article><span>行情錯誤</span><b>{health.dropped_ticks + health.worker_errors}</b><small>丟棄 {health.dropped_ticks} · Worker {health.worker_errors}</small></article>
        <article><span>Paper 復原</span><b>{health.paper_trading.status}</b><small>持倉 {health.paper_trading.restored_positions} · {value(health.paper_trading.recovery_duration_ms, " ms")}</small></article>
        <article><span>Paper 一致性</span><b>{health.paper_trading.recovery_issue_count}</b><small>受影響帳戶 {health.paper_trading.inconsistent_owners}</small></article>
        <article><span>Paper 委託</span><b>{health.paper_trading.submission_requests}</b><small>本次服務啟動後</small></article>
        <article><span>委託處理</span><b>{value(health.paper_trading.average_submission_ms, " ms")}</b><small>最大 {value(health.paper_trading.max_submission_ms, " ms")}</small></article>
      </div>
      <p className="system-health-note">每 10 秒更新；此頁只顯示彙總，不會揭露其他使用者的策略、委託或持倉。</p>
    </>}
  </section>;
}
