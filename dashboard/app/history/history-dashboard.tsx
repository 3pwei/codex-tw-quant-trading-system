"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type Summary = Record<string, number | null>;
type Run = {
  run_id: string; strategy_kind: "atomic" | "composite"; strategy_key: string;
  strategy_version: number | null; strategy_name: string; symbol: string;
  interval: string; start_date: string; end_date: string; status: string;
  created_at: string; trade_count: number; summary: Summary;
};
type Trade = {
  direction: "long" | "short"; entry_time: string; exit_time: string;
  entry_price: number; exit_price: number; net_pnl: number; total_cost: number;
  stop_loss_price?: number; take_profit_price?: number; exit_reason: string;
};
type Detail = Run & {
  strategy_snapshot: Record<string, unknown>;
  result: { config: Record<string, unknown>; trades: Trade[]; equity: Record<string, unknown>[] };
};

const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const money = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedMoney = (value: number) => `${value >= 0 ? "+" : "−"}NT$ ${money.format(Math.abs(value))}`;
const formatTime = (value: string) => new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });
const exitReason = (value: string) => ({ stop_loss: "停損", take_profit: "停利", force_exit: "時段平倉", end_of_data: "資料結束", mean_reversion: "回歸均線" }[value] ?? value);

export default function HistoryDashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadDetail = async (runId: string) => {
    setSelectedId(runId); setError("");
    const response = await fetch(`${apiBase()}/api/backtest-runs/${runId}`, { cache: "no-store" });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail ?? "無法取得回測明細");
    setDetail(body);
  };

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`${apiBase()}/api/backtest-runs?limit=500`, { cache: "no-store" });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "無法取得回測紀錄");
        setRuns(body.runs);
        if (body.runs.length) await loadDetail(body.runs[0].run_id);
      } catch (reason) { setError(reason instanceof Error ? reason.message : "無法取得回測紀錄"); }
      finally { setLoading(false); }
    })();
  }, []);

  const filtered = useMemo(() => runs.filter(run => {
    const matchesKind = kind === "all" || run.strategy_kind === kind;
    const needle = query.trim().toLowerCase();
    return matchesKind && (!needle || `${run.strategy_name} ${run.strategy_key}`.toLowerCase().includes(needle));
  }), [runs, query, kind]);

  if (loading) return <section className="panel history-loading">正在讀取回測紀錄…</section>;
  if (error && !runs.length) return <section className="feature-state panel"><span>HISTORY ERROR</span><h2>無法載入回測紀錄</h2><p>{error}</p></section>;
  if (!runs.length) return <section className="feature-state panel"><span>HISTORY READY</span><h2>尚無已保存的回測</h2><p>到歷史回測頁選擇策略與期間，按下「執行回測」後，結果就會保存在這裡。</p><div><Link href="/backtest/">建立第一筆回測</Link></div></section>;

  const summary = detail?.summary ?? {};
  const net = Number(summary.net_profit ?? 0);
  return <div className="history-layout">
    <aside className="panel history-index">
      <div className="history-filters"><input aria-label="搜尋策略" placeholder="搜尋策略名稱" value={query} onChange={event => setQuery(event.target.value)} /><select aria-label="策略類型" value={kind} onChange={event => setKind(event.target.value)}><option value="all">全部類型</option><option value="atomic">基本策略</option><option value="composite">組合策略</option></select></div>
      <div className="history-count">共 {filtered.length} 筆</div>
      <div className="history-runs">{filtered.map(run => <button type="button" className={selectedId === run.run_id ? "active" : ""} key={run.run_id} onClick={() => void loadDetail(run.run_id).catch(reason => setError(reason instanceof Error ? reason.message : "無法取得回測明細"))}><span><b>{run.strategy_name}{run.strategy_version ? ` · v${run.strategy_version}` : ""}</b><em>{run.strategy_kind === "composite" ? "組合" : run.interval}</em></span><small>{run.start_date} ～ {run.end_date}</small><strong className={Number(run.summary.net_profit ?? 0) >= 0 ? "profit" : "loss"}>{signedMoney(Number(run.summary.net_profit ?? 0))}</strong><time>{formatTime(run.created_at)}</time></button>)}</div>
    </aside>
    {detail && <section className="history-detail">
      {error && <div className="live-error">{error}</div>}
      <header className="panel history-detail-head"><div><span>BACKTEST RUN · {detail.run_id.slice(0, 8)}</span><h2>{detail.strategy_name}{detail.strategy_version ? ` · v${detail.strategy_version}` : ""}</h2><p>{detail.symbol} · {detail.start_date} ～ {detail.end_date} · {formatTime(detail.created_at)}</p></div><strong className={net >= 0 ? "profit" : "loss"}>{signedMoney(net)}</strong></header>
      <div className="history-metrics"><article><span>總報酬</span><b>{decimal.format(Number(summary.return_pct ?? 0))}%</b></article><article><span>最大回撤</span><b>{decimal.format(Number(summary.max_drawdown_pct ?? 0))}%</b></article><article><span>勝率</span><b>{decimal.format(Number(summary.win_rate_pct ?? 0))}%</b></article><article><span>交易次數</span><b>{detail.trade_count}</b></article><article><span>Profit Factor</span><b>{summary.profit_factor == null ? "N/A" : decimal.format(Number(summary.profit_factor))}</b></article></div>
      <section className="panel history-ledger"><div className="panel-head"><div><span>IMMUTABLE RESULT</span><h2>交易明細</h2></div><small>策略快照與結果已保存</small></div><div className="table-scroll"><table><thead><tr><th>#</th><th>方向</th><th>進場</th><th>出場</th><th>停損／停利</th><th>成本</th><th>淨損益</th><th>原因</th></tr></thead><tbody>{detail.result.trades.map((trade, index) => <tr key={`${trade.entry_time}-${index}`}><td>{index + 1}</td><td><i className={`dir ${trade.direction}`}>{trade.direction === "long" ? "多" : "空"}</i></td><td>{formatTime(trade.entry_time)}<small>{decimal.format(trade.entry_price)}</small></td><td>{formatTime(trade.exit_time)}<small>{decimal.format(trade.exit_price)}</small></td><td><span className="loss">{decimal.format(trade.stop_loss_price ?? 0)}</span><small className="profit">{decimal.format(trade.take_profit_price ?? 0)}</small></td><td>NT$ {money.format(trade.total_cost)}</td><td className={trade.net_pnl >= 0 ? "profit" : "loss"}><b>{signedMoney(trade.net_pnl)}</b></td><td>{exitReason(trade.exit_reason)}</td></tr>)}</tbody></table>{!detail.result.trades.length && <p className="history-empty-trades">此回測沒有產生完整交易。</p>}</div></section>
    </section>}
  </div>;
}
