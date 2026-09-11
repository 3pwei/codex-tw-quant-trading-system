"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import InteractiveTradeChart from "../backtest/interactive-trade-chart";
import {
  type BacktestChartPayload,
  type BacktestTrade,
} from "../backtest/trade-chart-model";
import { exitReasonLabel } from "../components/exit-reason";
import {
  batchDeletionPayload,
  toggleRunSelection,
} from "./history-selection";

type Summary = Record<string, number | null>;
type Run = {
  run_id: string; strategy_kind: "atomic" | "composite"; strategy_key: string;
  strategy_version: number | null; strategy_name: string; symbol: string;
  interval: string; start_date: string; end_date: string; status: string;
  created_at: string; trade_count: number; summary: Summary;
};
type Trade = BacktestTrade & { total_cost: number; exit_reason: string };
type Detail = Run & {
  strategy_snapshot: Record<string, unknown>;
  result: { config: Record<string, unknown>; trades: Trade[]; equity: Record<string, unknown>[] };
};

const apiBase = () => (process.env.NEXT_PUBLIC_MARKET_API_URL ?? (typeof window === "undefined" ? "" : window.location.origin)).replace(/\/$/, "");
const money = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("zh-TW", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const signedMoney = (value: number) => `${value >= 0 ? "+" : "−"}NT$ ${money.format(Math.abs(value))}`;
const formatTime = (value: string) => new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });
const PAGE_SIZE = 50;

export default function HistoryDashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [selectedTradeIndex, setSelectedTradeIndex] = useState(0);
  const [chart, setChart] = useState<BacktestChartPayload | null>(null);
  const [chartCache, setChartCache] = useState<Record<string, BacktestChartPayload>>({});
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("all");
  const [outcome, setOutcome] = useState("all");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [batchDeleting, setBatchDeleting] = useState(false);
  const [selectedRunIds, setSelectedRunIds] = useState<string[]>([]);
  const [allRunsSelected, setAllRunsSelected] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadChart = async (runId: string, tradeIndex: number) => {
    const cacheKey = `${runId}:${tradeIndex}`;
    const cached = chartCache[cacheKey];
    setSelectedTradeIndex(tradeIndex); setChartError("");
    if (cached) { setChart(cached); return; }
    setChartLoading(true);
    try {
      const response = await fetch(`${apiBase()}/api/backtest-runs/${runId}/chart?trade_index=${tradeIndex}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "無法取得交易圖表");
      const payload = body as BacktestChartPayload;
      setChart(payload);
      setChartCache(current => {
        const next = { ...current, [cacheKey]: payload };
        payload.trades.forEach(trade => {
          if (trade.trade_index != null) next[`${runId}:${trade.trade_index}`] = payload;
        });
        return next;
      });
    } catch (reason) {
      setChart(null);
      setChartError(reason instanceof Error ? reason.message : "無法取得交易圖表");
    } finally { setChartLoading(false); }
  };

  const loadDetail = async (runId: string) => {
    setSelectedId(runId); setDetail(null); setChart(null); setSelectedTradeIndex(0); setDetailLoading(true); setError(""); setChartError("");
    try {
      const response = await fetch(`${apiBase()}/api/backtest-runs/${runId}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "無法取得回測明細");
      setDetail(body);
      if (body.result?.trades?.length) await loadChart(runId, 0);
    } finally { setDetailLoading(false); }
  };

  const deleteRun = async () => {
    if (!detail || deleting) return;
    const noTrades = detail.trade_count === 0 ? "（此回測沒有產生交易）" : "";
    if (!window.confirm(`確定永久刪除這筆回測紀錄${noTrades}？\n\n刪除後無法復原，並會解除它對策略版本的引用。`)) return;
    setDeleting(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiBase()}/api/backtest-runs/${detail.run_id}`, { method: "DELETE" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "無法刪除回測紀錄");
      const remaining = runs.filter(run => run.run_id !== detail.run_id);
      setRuns(remaining); setSelectedId(""); setDetail(null); setChart(null);
      setSelectedRunIds(current => current.filter(runId => runId !== detail.run_id));
      setNotice(body.released_strategy_reference ? "回測紀錄已刪除，策略版本引用已解除。" : "回測紀錄已刪除。");
      if (remaining.length) await loadDetail(remaining[0].run_id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "無法刪除回測紀錄"); }
    finally { setDeleting(false); }
  };

  useEffect(() => {
    void (async () => {
      try {
        const response = await fetch(`${apiBase()}/api/backtest-runs?limit=${PAGE_SIZE}`, { cache: "no-store" });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "無法取得回測紀錄");
        setRuns(body.runs);
        setHasMore(Boolean(body.has_more));
      } catch (reason) { setError(reason instanceof Error ? reason.message : "無法取得回測紀錄"); }
      finally { setLoading(false); }
    })();
  }, []);

  const loadMore = async () => {
    if (!hasMore || loadingMore) return;
    setLoadingMore(true); setError("");
    try {
      const response = await fetch(`${apiBase()}/api/backtest-runs?limit=${PAGE_SIZE}&offset=${runs.length}`, { cache: "no-store" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "無法取得更多回測紀錄");
      setRuns(current => [...current, ...body.runs]);
      setHasMore(Boolean(body.has_more));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "無法取得更多回測紀錄"); }
    finally { setLoadingMore(false); }
  };

  const filtered = useMemo(() => runs.filter(run => {
    const matchesKind = kind === "all" || run.strategy_kind === kind;
    const matchesOutcome = outcome === "all" || (outcome === "empty" ? run.trade_count === 0 : run.trade_count > 0);
    const needle = query.trim().toLowerCase();
    return matchesKind && matchesOutcome && (!needle || `${run.strategy_name} ${run.strategy_key}`.toLowerCase().includes(needle));
  }), [runs, query, kind, outcome]);

  const deleteRuns = async () => {
    if (batchDeleting || (!allRunsSelected && !selectedRunIds.length)) return;
    const expected = allRunsSelected ? "刪除全部" : "永久刪除";
    const target = allRunsSelected
      ? "此帳號的所有執行記錄（包含尚未載入的頁面）"
      : `已選取的 ${selectedRunIds.length} 筆執行記錄`;
    const confirmation = window.prompt(
      `將永久刪除${target}，刪除後無法復原，並會解除策略版本引用。\n\n請輸入「${expected}」確認：`
    );
    if (confirmation !== expected) return;

    setBatchDeleting(true); setError(""); setNotice("");
    try {
      const response = await fetch(`${apiBase()}/api/backtest-runs`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batchDeletionPayload(allRunsSelected, selectedRunIds)),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "無法刪除執行記錄");

      const deleted = allRunsSelected ? new Set(runs.map(run => run.run_id)) : new Set(selectedRunIds);
      setRuns(current => allRunsSelected
        ? []
        : current.filter(run => !deleted.has(run.run_id)));
      if (allRunsSelected) setHasMore(false);
      setSelectedRunIds([]);
      setAllRunsSelected(false);
      if (allRunsSelected || deleted.has(selectedId)) {
        setSelectedId(""); setDetail(null); setChart(null);
      }
      const released = Number(body.released_strategy_references ?? 0);
      setNotice(
        `已永久刪除 ${body.deleted_runs} 筆執行記錄${released ? `，並解除 ${released} 筆策略版本引用` : ""}。`
      );
      try {
        const refreshed = await fetch(`${apiBase()}/api/backtest-runs?limit=${PAGE_SIZE}`, { cache: "no-store" });
        const refreshedBody = await refreshed.json();
        if (!refreshed.ok) throw new Error();
        setRuns(refreshedBody.runs);
        setHasMore(Boolean(refreshedBody.has_more));
      } catch {
        setError("刪除已完成，但清單重新載入失敗；請重新整理頁面。");
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "無法刪除執行記錄");
    } finally { setBatchDeleting(false); }
  };

  if (loading) return <div className="history-layout history-layout-loading" aria-busy="true" aria-label="正在讀取回測紀錄">
    <aside className="panel history-index history-index-loading">
      <div className="history-skeleton history-skeleton-control" />
      <div className="history-skeleton history-skeleton-count" />
      <div className="history-skeleton-list">
        {Array.from({ length: 6 }, (_, index) => <div className="history-skeleton history-skeleton-run" key={index} />)}
      </div>
    </aside>
    <section className="history-detail history-detail-loading">
      <div className="panel history-skeleton-detail">
        <div className="history-skeleton history-skeleton-kicker" />
        <div className="history-skeleton history-skeleton-title" />
        <div className="history-skeleton history-skeleton-copy" />
      </div>
      <div className="history-skeleton-metrics">
        {Array.from({ length: 5 }, (_, index) => <div className="history-skeleton" key={index} />)}
      </div>
      <div className="panel history-skeleton-ledger">
        <span className="history-loading-label">正在讀取回測紀錄…</span>
      </div>
    </section>
  </div>;
  if (error && !runs.length) return <section className="feature-state panel"><span>HISTORY ERROR</span><h2>無法載入回測紀錄</h2><p>{error}</p></section>;
  if (!runs.length) return <section className="feature-state panel"><span>HISTORY READY</span><h2>{notice || "尚無已保存的回測"}</h2><p>到歷史回測頁選擇策略與期間，按下「執行回測」後，結果就會保存在這裡。</p><div><Link href="/backtest/">建立第一筆回測</Link></div></section>;

  const summary = detail?.summary ?? {};
  const net = Number(summary.net_profit ?? 0);
  return <div className="history-layout">
    <aside className="panel history-index">
      <div className="history-filters"><input aria-label="搜尋策略" placeholder="搜尋策略名稱" value={query} onChange={event => setQuery(event.target.value)} /><select aria-label="策略類型" value={kind} onChange={event => setKind(event.target.value)}><option value="all">全部類型</option><option value="atomic">基本策略</option><option value="composite">組合策略</option></select><select aria-label="交易結果" value={outcome} onChange={event => setOutcome(event.target.value)}><option value="all">全部結果</option><option value="traded">有交易</option><option value="empty">無交易</option></select></div>
      <div className="history-count">顯示 {filtered.length} 筆 · 已載入 {runs.length} 筆</div>
      <div className="history-bulk-actions">
        <label><input type="checkbox" disabled={!runs.length || batchDeleting} checked={allRunsSelected} onChange={() => { setAllRunsSelected(current => !current); setSelectedRunIds([]); }} />全選全部紀錄（含未載入）</label>
        <div><button className="danger" type="button" disabled={(!allRunsSelected && !selectedRunIds.length) || batchDeleting} onClick={() => void deleteRuns()}>{batchDeleting ? "刪除中…" : `刪除已選（${allRunsSelected ? "全部" : selectedRunIds.length}）`}</button></div>
      </div>
      <div className="history-runs">{filtered.map(run => <article className={`history-run-row${selectedId === run.run_id ? " active" : ""}`} key={run.run_id}><input aria-label={`選取 ${run.strategy_name} ${run.start_date} 至 ${run.end_date}`} type="checkbox" disabled={batchDeleting || allRunsSelected} checked={allRunsSelected || selectedRunIds.includes(run.run_id)} onChange={() => setSelectedRunIds(current => toggleRunSelection(current, run.run_id))} /><button type="button" className="history-run-open" onClick={() => void loadDetail(run.run_id).catch(reason => setError(reason instanceof Error ? reason.message : "無法取得回測明細"))}><span><b>{run.strategy_name}{run.strategy_version ? ` · v${run.strategy_version}` : ""}</b><em>{run.strategy_kind === "composite" ? "組合" : run.interval}</em>{run.trade_count === 0 && <i>無交易</i>}</span><small>{run.start_date} ～ {run.end_date}</small><strong className={Number(run.summary.net_profit ?? 0) >= 0 ? "profit" : "loss"}>{signedMoney(Number(run.summary.net_profit ?? 0))}</strong><time>{formatTime(run.created_at)}</time></button></article>)}</div>
      {hasMore && <button className="history-load-more" type="button" disabled={loadingMore} onClick={() => void loadMore()}>{loadingMore ? "載入中…" : "載入更多"}</button>}
    </aside>
    <section className="history-detail">
      {notice && <div className="history-notice">{notice}</div>}
      {error && <div className="live-error">{error}</div>}
      {detailLoading && <div className="panel history-loading">正在讀取回測明細…</div>}
      {!detailLoading && !detail && runs.length > 0 && <div className="feature-state panel"><span>HISTORY READY</span><h2>清單已載入，請選擇一筆回測紀錄</h2><p>完整交易與績效明細只會在點選後載入。</p></div>}
      {!detailLoading && detail && <>
      <header className="panel history-detail-head"><div><span>BACKTEST RUN · {detail.run_id.slice(0, 8)}</span><h2>{detail.strategy_name}{detail.strategy_version ? ` · v${detail.strategy_version}` : ""}</h2><p>{detail.symbol} · {detail.start_date} ～ {detail.end_date} · {formatTime(detail.created_at)}</p></div><div className="history-result-actions"><strong className={net >= 0 ? "profit" : "loss"}>{signedMoney(net)}</strong><button type="button" disabled={deleting} onClick={() => void deleteRun()}>{deleting ? "刪除中…" : "永久刪除"}</button></div></header>
      <div className="history-metrics"><article><span>總報酬</span><b>{decimal.format(Number(summary.return_pct ?? 0))}%</b></article><article><span>最大回撤</span><b>{decimal.format(Number(summary.max_drawdown_pct ?? 0))}%</b></article><article><span>勝率</span><b>{decimal.format(Number(summary.win_rate_pct ?? 0))}%</b></article><article><span>交易次數</span><b>{detail.trade_count}</b></article><article><span>Profit Factor</span><b>{summary.profit_factor == null ? "N/A" : decimal.format(Number(summary.profit_factor))}</b></article></div>
      {chartLoading && <section className="panel history-trade-chart history-chart-state">正在載入交易時段 K 棒…</section>}
      {!chartLoading && chart && <section className="panel history-trade-chart"><div className="panel-head"><div><span>TRADE EXPLORER</span><h2>{chart.scope.label} · {chart.scope.contract}</h2></div><small>{chart.scope.kind === "range" ? "日／週 K 回測區間" : "同時段交易共用一張圖"}</small></div><InteractiveTradeChart key={chart.scope.key} bars={chart.bars} trades={chart.trades} selectedTrade={chart.trades.find(trade => trade.trade_index === selectedTradeIndex) ?? detail.result.trades[selectedTradeIndex]} overlays={chart.overlays} rangeMode={chart.scope.kind === "range"}/></section>}
      {!chartLoading && chartError && <section className="panel history-trade-chart history-chart-state"><b>交易明細仍可使用</b><p>{chartError}</p></section>}
      <section className="panel history-ledger"><div className="panel-head"><div><span>SAVED RESULT</span><h2>交易明細</h2></div><small>點選交易可聚焦進出場 K 棒</small></div><div className="table-scroll"><table><thead><tr><th>#</th><th>方向</th><th>進場</th><th>出場</th><th>停損／停利</th><th>成本</th><th>淨損益</th><th>原因</th></tr></thead><tbody>{detail.result.trades.map((trade, index) => <tr key={`${trade.entry_time}-${index}`} className={selectedTradeIndex === index ? "selected" : ""} onClick={() => void loadChart(detail.run_id, index)}><td>{index + 1}</td><td><i className={`dir ${trade.direction}`}>{trade.direction === "long" ? "多" : "空"}</i></td><td>{formatTime(trade.entry_time)}<small>{decimal.format(trade.entry_price)}</small></td><td>{formatTime(trade.exit_time)}<small>{decimal.format(trade.exit_price)}</small></td><td><span className="loss">{decimal.format(trade.stop_loss_price ?? 0)}</span><small className="profit">{decimal.format(trade.take_profit_price ?? 0)}</small></td><td>NT$ {money.format(trade.total_cost)}</td><td className={trade.net_pnl >= 0 ? "profit" : "loss"}><b>{signedMoney(trade.net_pnl)}</b></td><td>{exitReasonLabel(trade.exit_reason)}</td></tr>)}</tbody></table>{!detail.result.trades.length && <p className="history-empty-trades">此回測沒有產生完整交易。</p>}</div></section>
      </>}
    </section>
  </div>;
}
