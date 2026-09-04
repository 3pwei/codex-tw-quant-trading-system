"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import SectionShell from "./components/section-shell";

type Health = {
  connection_status: "connecting" | "connected" | "reconnecting" | "disconnected";
  contract: string;
  last_tick_time: string | null;
  latency_ms: number | null;
  history_bars_loaded: number;
};
type KBar = { close: number; session: "day" | "night"; status: "forming" | "closed"; time: string };
type Strategy = { key: string; name: string; signals: { event: "entry" | "exit"; direction: "long" | "short"; time: string; price: number }[] };

const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const number = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 });
const time = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(value)) : "等待行情";

const modules = [
  { href: "/live/", code: "LIVE", title: "即時交易／1 分 K", detail: "WebSocket 行情、形成中 K 棒與策略訊號", ready: true },
  { href: "/backtest/", code: "TEST", title: "歷史回測", detail: "選擇策略與最長 31 天的歷史範圍", ready: true },
  { href: "/replay/", code: "PLAY", title: "動態歷史回放", detail: "依時間軸模擬行情逐根推進", ready: false },
  { href: "/history/", code: "LOG", title: "回測／交易歷史", detail: "查看績效、交易明細與策略版本", ready: true },
  { href: "/strategies/", code: "STR", title: "策略管理", detail: "ORB、BNF 規則與風險參數", ready: true },
  { href: "/settings/", code: "SET", title: "系統設定", detail: "行情、商品、連線與部署設定", ready: true },
] as const;

export default function OverviewDashboard() {
  const [health, setHealth] = useState<Health | null>(null);
  const [bar, setBar] = useState<KBar | null>(null);
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [healthResponse, barsResponse, strategyResponse] = await Promise.all([
        fetch(`${apiBase()}/api/health`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/kbars?symbol=TMF&interval=1m&limit=1`, { cache: "no-store" }),
        fetch(`${apiBase()}/api/strategy-signals?symbol=TMF&strategies=orb,bnf&limit=500`, { cache: "no-store" }),
      ]);
      if (!healthResponse.ok || !barsResponse.ok || !strategyResponse.ok) throw new Error("系統狀態 API 回應異常");
      const bars: KBar[] = await barsResponse.json();
      const strategyBody: { strategies: Strategy[] } = await strategyResponse.json();
      setHealth(await healthResponse.json());
      setBar(bars.at(-1) ?? null);
      setStrategies(strategyBody.strategies);
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法取得系統狀態");
    }
  }, []);

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 10_000);
    return () => {
      window.clearTimeout(initial);
      window.clearInterval(timer);
    };
  }, [refresh]);

  const connected = health?.connection_status === "connected";
  return (
    <SectionShell active="/" eyebrow="WADE QUANT LAB · SYSTEM 01" title="TMF 量化交易系統" description="系統總覽、目前行情與策略狀態">
      <section className="overview-status">
        <article><span>行情連線</span><strong className={connected ? "profit" : "warning"}>{connected ? "即時連線" : health?.connection_status ?? "讀取中"}</strong><small>{health?.contract ?? "TMF"}</small></article>
        <article><span>最新成交價</span><strong>{bar ? number.format(bar.close) : "—"}</strong><small>{bar?.session === "night" ? "夜盤" : bar?.session === "day" ? "日盤" : "尚無行情"} · {bar?.status === "forming" ? "形成中" : "已收盤"}</small></article>
        <article><span>最後行情時間</span><strong>{time(health?.last_tick_time)}</strong><small>交易所時間 · Asia/Taipei</small></article>
        <article><span>資料延遲</span><strong className={(health?.latency_ms ?? 0) > 1000 ? "warning" : "profit"}>{health?.latency_ms == null ? "—" : `${number.format(health.latency_ms)} ms`}</strong><small>歷史載入 {health?.history_bars_loaded ?? 0} 根</small></article>
      </section>
      {error && <div className="live-error">{error}；總覽將自動重試，其他功能仍可由下方進入。</div>}
      <section className="overview-grid">
        {modules.map(module => <Link key={module.href} href={module.href} className="module-card">
          <div><b>{module.code}</b><span className={module.ready ? "ready" : "planned"}>{module.ready ? "可使用" : "建置中"}</span></div>
          <h2>{module.title}</h2><p>{module.detail}</p><em>開啟功能 →</em>
        </Link>)}
      </section>
      <section className="strategy-overview panel">
        <div className="panel-head"><div><span>STRATEGY STATUS</span><h2>策略訊號概況</h2></div><Link href="/strategies/">查看策略規則 →</Link></div>
        <div className="strategy-overview-grid">
          {strategies.map(strategy => {
            const latest = strategy.signals.at(-1);
            return <article key={strategy.key}><span>{strategy.key.toUpperCase()}</span><strong>{strategy.name}</strong><b>{latest ? `${latest.event === "entry" ? "進場" : "出場"} · ${latest.direction === "long" ? "做多" : "做空"}` : "目前無訊號"}</b><small>{latest ? `${time(latest.time)} · ${number.format(latest.price)}` : "等待符合條件"}</small></article>;
          })}
          {!strategies.length && <p className="muted-copy">正在讀取 ORB／BNF 策略狀態…</p>}
        </div>
      </section>
    </SectionShell>
  );
}
