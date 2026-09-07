"use client";

import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries,
  type CandlestickData, type HistogramData, type IChartApi, type ISeriesApi,
  type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";

type Session = "day" | "night";
type ReplayBar = { time: string; end_time: string; open: number; high: number; low: number; close: number; volume: number; contract: string; session: Session; trading_date: string; no_trade: boolean };
type ReplaySignal = { strategy: string; event: "entry" | "exit"; direction: "long" | "short"; time: string; price: number; stop_loss_price: number; take_profit_price: number; reason: string };
type ReplayStrategy = { key: string; name: string; color: string; kind?: "composite"; version?: number; signals: ReplaySignal[] };
type ReplayAccount = { realized_pnl: number; open_contracts: number; trades: number };
type ReplayPosition = { strategy_id: string; strategy_version: number; contract: string; quantity: number; average_price: number; unrealized_pnl: number };
type ReplayOrder = { order_id: string; submitted_at: string; strategy_id: string; strategy_version: number; contract: string; side: "buy" | "sell"; quantity: number; reduce_only: boolean; reference_price: number; stop_loss_price: number | null; status: "pending_risk" | "approved" | "rejected" | "filled"; status_reason: string };
type ReplayFill = { fill_id: string; contract: string; side: "buy" | "sell"; quantity: number; price: number; commission: number; tax: number; slippage: number; purpose: "entry" | "exit" | "liquidation"; meta: { occurred_at: string } };
type ReplayTradingState = { session_id: string; snapshot_id: string; mode: "replay"; isolated_from_live_paper: true; cursor: number; bar_count: number; virtual_time: string; rewound: boolean; account: ReplayAccount; positions: ReplayPosition[]; orders: ReplayOrder[]; fills: ReplayFill[] };
type ReplaySnapshot = { snapshot_id: string; created_at: string; symbol: string; trading_date: string; session: Session; interval: string; interval_name: string; bars: ReplayBar[]; strategies: ReplayStrategy[]; trading_session: ReplayTradingState };
type StrategyOption = { key: string; name: string; kind: "atomic" | "composite"; color: string };
type Availability = { date: string; sessions: { key: Session; bar_count: number }[] };
type ReplayOptions = { available_start: string | null; available_end: string | null; available_dates: Availability[]; intervals: { key: string; name: string }[]; strategies: StrategyOption[]; max_strategies: number; sessions: { key: Session; name: string }[] };

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_MARKET_API_URL?.replace(/\/$/, "");
const apiBase = () => CONFIGURED_API_BASE || (typeof window === "undefined" ? "http://localhost:8000" : window.location.origin);
const asTime = (value: string) => Math.floor(Date.parse(value) / 1000) as UTCTimestamp;
const fmtPrice = (value?: number | null) => value == null ? "—" : new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 }).format(value);
const fmtClock = (value?: string) => value ? new Date(value).toLocaleTimeString("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
const fmtMoney = (value: number) => `${value >= 0 ? "+" : "−"}NT$ ${new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(Math.abs(value))}`;
const chartClock = (value: Time) => {
  const date = typeof value === "number" ? new Date(value * 1000) : new Date(String(value));
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(date);
};

function marker(strategy: ReplayStrategy, signal: ReplaySignal, time = signal.time): SeriesMarker<Time> {
  const entry = signal.event === "entry", long = signal.direction === "long";
  return {
    time: asTime(time),
    position: long ? entry ? "belowBar" : "aboveBar" : entry ? "aboveBar" : "belowBar",
    color: entry ? strategy.color : "#f5b942",
    shape: entry ? long ? "arrowUp" : "arrowDown" : "circle",
    text: `${strategy.name} · ${entry ? long ? "多進" : "空進" : "出場"} ${fmtPrice(signal.price)}`,
  };
}

export default function ReplayDashboard() {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const replaySessionRef = useRef<string | null>(null);
  const cursorSyncRef = useRef<Promise<boolean>>(Promise.resolve(true));
  const [options, setOptions] = useState<ReplayOptions | null>(null);
  const [date, setDate] = useState("");
  const [session, setSession] = useState<Session>("day");
  const [interval, setIntervalValue] = useState("1m");
  const [selected, setSelected] = useState<string[]>(["orb"]);
  const [snapshot, setSnapshot] = useState<ReplaySnapshot | null>(null);
  const [cursor, setCursor] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(2);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [trading, setTrading] = useState<ReplayTradingState | null>(null);
  const [tradeSide, setTradeSide] = useState<"buy" | "sell">("buy");
  const [tradeQuantity, setTradeQuantity] = useState(1);
  const [tradeStop, setTradeStop] = useState("");
  const [tradeTab, setTradeTab] = useState<"positions" | "orders" | "fills">("positions");
  const [tradeBusy, setTradeBusy] = useState("");
  const [tradeNotice, setTradeNotice] = useState("");

  useEffect(() => {
    let active = true;
    fetch(`${apiBase()}/api/replay/options?symbol=TMF`, { cache: "no-store" })
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "回放選項載入失敗");
        if (!active) return;
        const value = body as ReplayOptions;
        setOptions(value);
        const latest = value.available_dates[value.available_dates.length - 1];
        setDate(latest?.date ?? value.available_end ?? "");
        setSession(latest?.sessions.some(item => item.key === "day") ? "day" : (latest?.sessions[0]?.key ?? "day"));
        setLoading(false);
      })
      .catch(reason => { if (active) { setError(reason instanceof Error ? reason.message : "回放選項載入失敗"); setLoading(false); } });
    return () => { active = false; };
  }, []);

  const availability = useMemo(() => options?.available_dates.find(item => item.date === date), [date, options]);
  const chooseDate = (nextDate: string) => {
    setDate(nextDate);
    const next = options?.available_dates.find(item => item.date === nextDate);
    if (next && !next.sessions.some(item => item.key === session)) setSession(next.sessions[0]?.key ?? "day");
  };

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = createChart(hostRef.current, {
      width: hostRef.current.clientWidth, height: 560,
      layout: { background: { type: ColorType.Solid, color: "#07111f" }, textColor: "#9fb0c7", panes: { separatorColor: "#17283b" } },
      grid: { vertLines: { color: "#132237" }, horzLines: { color: "#132237" } },
      timeScale: { borderColor: "#26384d", timeVisible: true, secondsVisible: false, rightOffset: 8, tickMarkFormatter: chartClock },
      rightPriceScale: { borderColor: "#26384d" }, localization: { locale: "zh-TW", timeFormatter: chartClock },
    });
    const candles = chart.addSeries(CandlestickSeries, { upColor: "#2dd4bf", downColor: "#f87171", borderVisible: false, wickUpColor: "#2dd4bf", wickDownColor: "#f87171", priceFormat: { type: "price", precision: 0, minMove: 1 } }, 0);
    const volumes = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" }, 1);
    chart.panes()[1]?.setHeight(110);
    chartRef.current = chart; candleRef.current = candles; volumeRef.current = volumes; markerRef.current = createSeriesMarkers(candles, []);
    const observer = new ResizeObserver(entries => { const width = Math.floor(entries[0]?.contentRect.width ?? 0); if (width > 0) chart.applyOptions({ width, height: window.innerWidth < 700 ? 460 : 560 }); });
    observer.observe(hostRef.current);
    return () => { observer.disconnect(); chart.remove(); chartRef.current = null; candleRef.current = null; volumeRef.current = null; markerRef.current = null; };
  }, [snapshot]);

  const paint = useCallback((nextCursor: number, fit = false) => {
    if (!snapshot?.bars.length) return;
    const count = Math.max(1, Math.min(nextCursor + 1, snapshot.bars.length));
    const bars = snapshot.bars.slice(0, count);
    candleRef.current?.setData(bars.map(bar => ({ time: asTime(bar.time), open: bar.open, high: bar.high, low: bar.low, close: bar.close } as CandlestickData<UTCTimestamp>)));
    volumeRef.current?.setData(bars.map(bar => ({ time: asTime(bar.time), value: bar.volume, color: bar.no_trade ? "rgba(148,163,184,.3)" : bar.close >= bar.open ? "rgba(45,212,191,.45)" : "rgba(248,113,113,.45)" } as HistogramData<UTCTimestamp>)));
    const now = Date.parse(bars[bars.length - 1].end_time);
    const strategyMarkers = snapshot.strategies.flatMap(strategy => strategy.signals.filter(signal => Date.parse(signal.time) <= now).map(signal => {
      const signalAt = Date.parse(signal.time);
      const anchor = [...bars].reverse().find(bar => Date.parse(bar.time) <= signalAt) ?? bars[0];
      return marker(strategy, signal, anchor.time);
    }));
    const fillMarkers: SeriesMarker<Time>[] = (trading?.fills ?? []).filter(fill => Date.parse(fill.meta.occurred_at) <= now).map(fill => {
      const fillAt = Date.parse(fill.meta.occurred_at);
      const anchor = [...bars].reverse().find(bar => Date.parse(bar.time) <= fillAt) ?? bars[0];
      return {
        time: asTime(anchor.time),
        position: fill.side === "buy" ? "belowBar" : "aboveBar",
        color: fill.side === "buy" ? "#42d6a4" : "#ff6b72",
        shape: fill.side === "buy" ? "arrowUp" : "arrowDown",
        text: `REPLAY ${fill.purpose === "entry" ? "成交" : "平倉"} ${fill.quantity}口 @ ${fmtPrice(fill.price)}`,
      };
    });
    markerRef.current?.setMarkers([...strategyMarkers, ...fillMarkers].sort((a, b) => Number(a.time) - Number(b.time)));
    if (fit) chartRef.current?.timeScale().fitContent(); else chartRef.current?.timeScale().scrollToRealTime();
  }, [snapshot, trading?.fills]);

  useEffect(() => { paint(cursor); }, [cursor, paint]);
  useEffect(() => {
    if (!playing || !snapshot) return;
    const timer = window.setInterval(() => setCursor(value => { if (value >= snapshot.bars.length - 1) { setPlaying(false); return value; } return value + 1; }), Math.max(50, 1000 / speed));
    return () => window.clearInterval(timer);
  }, [playing, snapshot, speed]);

  const prepare = async () => {
    if (!date || !selected.length) return;
    setLoading(true); setError(""); setTradeNotice(""); setPlaying(false);
    try {
      const response = await fetch(`${apiBase()}/api/replay/prepare`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: "TMF", trading_date: date, session, interval, strategies: selected }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "回放快照建立失敗");
      const prepared = body as ReplaySnapshot;
      replaySessionRef.current = prepared.trading_session.session_id;
      cursorSyncRef.current = Promise.resolve(true);
      setSnapshot(prepared); setTrading(prepared.trading_session); setCursor(0);
      setTradeTab("positions"); setTradeSide("buy"); setTradeQuantity(1);
      setTradeStop(String(prepared.bars[0].close - 50));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "回放快照建立失敗"); }
    finally { setLoading(false); }
  };

  const toggleStrategy = (key: string) => setSelected(current => current.includes(key) ? current.filter(item => item !== key) : current.length < (options?.max_strategies ?? 3) ? [...current, key] : current);
  const current = snapshot?.bars[cursor];
  const progress = snapshot?.bars.length ? Math.round((cursor + 1) / snapshot.bars.length * 100) : 0;
  const visibleSignals = snapshot?.strategies.map(strategy => ({ ...strategy, visible: strategy.signals.filter(signal => !current || Date.parse(signal.time) <= Date.parse(current.end_time)) })) ?? [];

  const syncCursor = useCallback((nextCursor: number) => {
    const requestedSession = replaySessionRef.current;
    if (!requestedSession) return Promise.resolve(false);
    const task = cursorSyncRef.current.catch(() => false).then(async () => {
      if (replaySessionRef.current !== requestedSession) return false;
      const response = await fetch(`${apiBase()}/api/replay/sessions/${requestedSession}/cursor`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cursor: nextCursor }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "回放交易時間同步失敗");
      if (replaySessionRef.current !== requestedSession) return false;
      const state = body as ReplayTradingState;
      setTrading(state);
      if (state.rewound) setTradeNotice("時間軸已倒退，Replay 帳戶與所有模擬成交已重設。");
      return true;
    }).catch(reason => {
      setPlaying(false);
      setError(reason instanceof Error ? reason.message : "回放交易時間同步失敗");
      return false;
    });
    cursorSyncRef.current = task;
    return task;
  }, []);

  useEffect(() => {
    if (snapshot?.trading_session.session_id) void syncCursor(cursor);
  }, [cursor, snapshot?.trading_session.session_id, syncCursor]);

  const chooseTradeSide = (nextSide: "buy" | "sell") => {
    setTradeSide(nextSide);
    if (current) setTradeStop(String(current.close + (nextSide === "buy" ? -50 : 50)));
  };

  const submitReplayOrder = async (event: FormEvent) => {
    event.preventDefault();
    const sessionId = replaySessionRef.current;
    if (!sessionId || !current) return;
    setPlaying(false); setTradeBusy("order"); setError(""); setTradeNotice("");
    try {
      if (!await syncCursor(cursor)) throw new Error("回放交易時間尚未同步");
      const response = await fetch(`${apiBase()}/api/replay/sessions/${sessionId}/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ strategy_id: "manual-replay", strategy_version: 1, side: tradeSide, quantity: tradeQuantity, stop_loss_price: Number(tradeStop) }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Replay 模擬委託失敗");
      setTrading(body.session as ReplayTradingState);
      setTradeNotice(body.order.status === "filled" ? `Replay ${tradeSide === "buy" ? "買進" : "賣出"} ${tradeQuantity} 口已成交` : `委託未成交：${body.order.status_reason}`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Replay 模擬委託失敗"); }
    finally { setTradeBusy(""); }
  };

  const closeReplayPosition = async (position: ReplayPosition) => {
    const sessionId = replaySessionRef.current;
    if (!sessionId || !current || !window.confirm(`確定以 Replay 目前價格平倉 ${position.contract} ${Math.abs(position.quantity)} 口？`)) return;
    setPlaying(false); setTradeBusy(`close:${position.contract}`); setError(""); setTradeNotice("");
    try {
      if (!await syncCursor(cursor)) throw new Error("回放交易時間尚未同步");
      const response = await fetch(`${apiBase()}/api/replay/sessions/${sessionId}/orders`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ strategy_id: position.strategy_id, strategy_version: position.strategy_version, side: position.quantity > 0 ? "sell" : "buy", quantity: Math.abs(position.quantity), reduce_only: true }),
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Replay 平倉失敗");
      setTrading(body.session as ReplayTradingState); setTradeNotice(`${position.contract} 已完成 Replay 平倉`);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Replay 平倉失敗"); }
    finally { setTradeBusy(""); }
  };

  const resetReplayTrading = async () => {
    const sessionId = replaySessionRef.current;
    if (!sessionId || !window.confirm("確定清除這次 Replay 的持倉、委託與成交？")) return;
    setPlaying(false); setTradeBusy("reset"); setError("");
    try {
      const response = await fetch(`${apiBase()}/api/replay/sessions/${sessionId}/reset`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "Replay 帳戶重設失敗");
      cursorSyncRef.current = Promise.resolve(true); setCursor(0); setTrading(body as ReplayTradingState);
      setTradeNotice("Replay 帳戶已重設，正式 Paper 帳戶不受影響。");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Replay 帳戶重設失敗"); }
    finally { setTradeBusy(""); }
  };

  return <>
    <section className="replay-config panel">
      <div className="replay-config-grid">
        <label><span>交易日</span><select value={date} onChange={event => chooseDate(event.target.value)} disabled={loading}>{options?.available_dates.slice().reverse().map(item => <option key={item.date} value={item.date}>{item.date}</option>)}</select></label>
        <label><span>交易時段</span><select value={session} onChange={event => setSession(event.target.value as Session)} disabled={loading}>{options?.sessions.filter(item => availability?.sessions.some(value => value.key === item.key)).map(item => <option key={item.key} value={item.key}>{item.name}</option>)}</select></label>
        <label><span>K 棒週期</span><select value={interval} onChange={event => setIntervalValue(event.target.value)} disabled={loading}>{options?.intervals.map(item => <option key={item.key} value={item.key}>{item.name}</option>)}</select></label>
        <button onClick={prepare} disabled={loading || !date || !selected.length}>{loading ? "準備中…" : "建立回放"}</button>
      </div>
      <div className="replay-strategy-picker"><header><span>疊加策略（最多 {options?.max_strategies ?? 3} 個）</span><small>訊號只會在時間軸抵達後出現</small></header><div>{options?.strategies.map(item => <button key={item.key} type="button" className={selected.includes(item.key) ? "active" : ""} onClick={() => toggleStrategy(item.key)} disabled={!selected.includes(item.key) && selected.length >= (options?.max_strategies ?? 3)}><i style={{ background: item.color }} />{item.kind === "composite" ? "組合 · " : ""}{item.name}</button>)}</div></div>
    </section>
    {error && <div className="live-error">{error}</div>}
    {!snapshot && !loading && !error && <section className="replay-empty panel"><b>選擇一個歷史盤次開始回放</b><p>建立快照後可播放、暫停、調速、逐根前進或拖曳時間軸。</p></section>}
    {snapshot && <>
      <section className="replay-player panel">
        <div className="replay-player-head"><div><span>REPLAY SNAPSHOT · {snapshot.snapshot_id.slice(0, 8).toUpperCase()}</span><h2>{snapshot.symbol} · {snapshot.trading_date} · {snapshot.session === "day" ? "日盤" : "夜盤"}</h2></div><div className="replay-quote"><small>{fmtClock(current?.time)} · {snapshot.interval_name}</small><strong>{fmtPrice(current?.close)}</strong><em>{progress}%</em></div></div>
        <div className="replay-chart" ref={hostRef} />
        <div className="replay-controls"><button onClick={() => { setPlaying(false); setCursor(0); }} aria-label="回到開頭">↺</button><button onClick={() => { setPlaying(false); setCursor(value => Math.max(0, value - 1)); }} aria-label="上一根">｜◀</button><button className="play" onClick={() => { if (cursor >= snapshot.bars.length - 1) setCursor(0); setPlaying(value => !value); }}>{playing ? "暫停" : "播放"}</button><button onClick={() => { setPlaying(false); setCursor(value => Math.min(snapshot.bars.length - 1, value + 1)); }} aria-label="下一根">▶｜</button><label><span>速度</span><select value={speed} onChange={event => setSpeed(Number(event.target.value))}>{[0.5, 1, 2, 5, 10].map(value => <option key={value} value={value}>{value}×</option>)}</select></label><input aria-label="回放進度" type="range" min={0} max={Math.max(0, snapshot.bars.length - 1)} value={cursor} onChange={event => { setPlaying(false); setCursor(Number(event.target.value)); }} /><small>{cursor + 1} / {snapshot.bars.length} 根</small></div>
      </section>
      <section className="replay-trading panel">
        <header className="replay-trading-head"><div><span>ISOLATED REPLAY TRADING</span><h2>歷史情境模擬下單</h2><p>只使用目前回放時間與價格；帳戶、持倉、委託及成交皆不會寫入正式 Paper。</p></div><div><b>REPLAY MODE</b><small>虛擬時間 {fmtClock(trading?.virtual_time)}</small></div></header>
        {tradeNotice && <div className="replay-trade-notice">{tradeNotice}</div>}
        <div className="replay-trade-metrics">
          <article><span>未實現損益</span><strong className={(trading?.positions.reduce((sum, item) => sum + item.unrealized_pnl, 0) ?? 0) >= 0 ? "profit" : "loss"}>{fmtMoney(trading?.positions.reduce((sum, item) => sum + item.unrealized_pnl, 0) ?? 0)}</strong></article>
          <article><span>已實現損益</span><strong className={(trading?.account.realized_pnl ?? 0) >= 0 ? "profit" : "loss"}>{fmtMoney(trading?.account.realized_pnl ?? 0)}</strong></article>
          <article><span>目前持倉</span><strong>{trading?.account.open_contracts ?? 0} 口</strong></article>
          <article><span>Replay 成交</span><strong>{trading?.account.trades ?? 0} 筆</strong></article>
        </div>
        <div className="replay-trade-grid">
          <form className="replay-order-ticket" onSubmit={submitReplayOrder}>
            <div><span>目前模擬價格</span><strong>{current?.contract ?? "—"} · {fmtPrice(current?.close)}</strong></div>
            <div className="replay-order-side"><button type="button" className={tradeSide === "buy" ? "buy active" : "buy"} onClick={() => chooseTradeSide("buy")}>買進／做多</button><button type="button" className={tradeSide === "sell" ? "sell active" : "sell"} onClick={() => chooseTradeSide("sell")}>賣出／做空</button></div>
            <label><span>數量</span><select value={tradeQuantity} onChange={event => setTradeQuantity(Number(event.target.value))}><option value={1}>1 口</option><option value={2}>2 口</option></select></label>
            <label><span>停損價</span><input required min="1" step="1" inputMode="decimal" value={tradeStop} onChange={event => setTradeStop(event.target.value)} /></label>
            <button className={`replay-order-submit ${tradeSide}`} disabled={!current || Boolean(tradeBusy)}>{tradeBusy === "order" ? "送單中…" : `以 ${fmtPrice(current?.close)} 模擬${tradeSide === "buy" ? "買進" : "賣出"}`}</button>
            <small>成交價與時間由伺服器依 Replay 游標決定，仍套用相同成本、持倉上限與停損風控。</small>
          </form>
          <div className="replay-trade-ledger">
            <div className="replay-trade-tabs" role="tablist" aria-label="Replay 交易紀錄">
              <button type="button" className={tradeTab === "positions" ? "active" : ""} onClick={() => setTradeTab("positions")}>持倉 <b>{trading?.positions.length ?? 0}</b></button>
              <button type="button" className={tradeTab === "orders" ? "active" : ""} onClick={() => setTradeTab("orders")}>委託 <b>{trading?.orders.length ?? 0}</b></button>
              <button type="button" className={tradeTab === "fills" ? "active" : ""} onClick={() => setTradeTab("fills")}>成交 <b>{trading?.fills.length ?? 0}</b></button>
            </div>
            <div className="replay-trade-list">
              {tradeTab === "positions" && <>{trading?.positions.map(position => <article key={`${position.strategy_id}:${position.contract}`}><div><b>{position.contract}</b><span className={position.quantity > 0 ? "profit" : "loss"}>{position.quantity > 0 ? "多" : "空"} {Math.abs(position.quantity)} 口</span></div><strong>均價 {fmtPrice(position.average_price)} · {fmtMoney(position.unrealized_pnl)}</strong><button type="button" disabled={Boolean(tradeBusy)} onClick={() => void closeReplayPosition(position)}>{tradeBusy === `close:${position.contract}` ? "平倉中…" : "全部平倉"}</button></article>)}{!trading?.positions.length && <p>目前沒有 Replay 持倉。</p>}</>}
              {tradeTab === "orders" && <>{trading?.orders.slice(0, 20).map(order => <article key={order.order_id}><div><b>{order.side === "buy" ? "買進" : "賣出"} {order.quantity} 口</b><span>{order.status === "filled" ? "已成交" : order.status === "rejected" ? "已拒絕" : "處理中"}</span></div><strong>{order.contract} · {fmtPrice(order.reference_price)}</strong><small>{fmtClock(order.submitted_at)} · {order.status_reason}</small></article>)}{!trading?.orders.length && <p>尚無 Replay 委託。</p>}</>}
              {tradeTab === "fills" && <>{trading?.fills.slice(0, 20).map(fill => <article key={fill.fill_id}><div><b>{fill.side === "buy" ? "買進" : "賣出"} {fill.quantity} 口</b><span>已成交</span></div><strong>{fill.contract} · {fmtPrice(fill.price)}</strong><small>{fmtClock(fill.meta.occurred_at)} · 成本 {fmtMoney(-(fill.commission + fill.tax))}</small></article>)}{!trading?.fills.length && <p>尚無 Replay 成交。</p>}</>}
            </div>
          </div>
        </div>
        <footer className="replay-trade-footer"><span>時間軸倒退會自動清除 Replay 交易，避免使用未來資訊。</span><button type="button" disabled={Boolean(tradeBusy)} onClick={() => void resetReplayTrading()}>{tradeBusy === "reset" ? "重設中…" : "重設 Replay 帳戶"}</button></footer>
      </section>
      <section className="replay-lower">
        <div className="replay-strategy-status panel"><header><span>STRATEGY EVENTS</span><h2>截至目前的策略訊號</h2></header>{visibleSignals.map(strategy => { const latest = strategy.visible[strategy.visible.length - 1]; return <article key={strategy.key}><div><i style={{ background: strategy.color }} /><strong>{strategy.name}</strong><small>{strategy.visible.length} 個訊號</small></div>{latest ? <dl><div><dt>狀態</dt><dd>{latest.event === "entry" ? latest.direction === "long" ? "多單進場" : "空單進場" : "已出場"}</dd></div><div><dt>時間</dt><dd>{fmtClock(latest.time)}</dd></div><div><dt>價格</dt><dd>{fmtPrice(latest.price)}</dd></div><div><dt>停損 / 停利</dt><dd>{fmtPrice(latest.stop_loss_price)} / {fmtPrice(latest.take_profit_price)}</dd></div></dl> : <p>時間軸尚未出現訊號</p>}</article>; })}</div>
        <aside className="replay-now panel"><span>NOW PLAYING</span><h2>{fmtClock(current?.time)}</h2><dl><div><dt>開</dt><dd>{fmtPrice(current?.open)}</dd></div><div><dt>高</dt><dd>{fmtPrice(current?.high)}</dd></div><div><dt>低</dt><dd>{fmtPrice(current?.low)}</dd></div><div><dt>收</dt><dd>{fmtPrice(current?.close)}</dd></div><div><dt>量</dt><dd>{fmtPrice(current?.volume)}</dd></div><div><dt>合約</dt><dd>{current?.contract ?? "—"}</dd></div></dl><p>這是使用者專屬的歷史快照；回放交易具有獨立虛擬帳戶，不會影響即時行情或正式 Paper。</p></aside>
      </section>
    </>}
  </>;
}
