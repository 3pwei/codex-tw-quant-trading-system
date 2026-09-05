"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries,
  type CandlestickData, type HistogramData, type IChartApi, type ISeriesApi,
  type ISeriesMarkersPluginApi, type SeriesMarker, type Time, type UTCTimestamp,
} from "lightweight-charts";

type Session = "day" | "night";
type ReplayBar = { time: string; end_time: string; open: number; high: number; low: number; close: number; volume: number; contract: string; session: Session; trading_date: string; no_trade: boolean };
type ReplaySignal = { strategy: string; event: "entry" | "exit"; direction: "long" | "short"; time: string; price: number; stop_loss_price: number; take_profit_price: number; reason: string };
type ReplayStrategy = { key: string; name: string; color: string; kind?: "composite"; version?: number; signals: ReplaySignal[] };
type ReplaySnapshot = { snapshot_id: string; created_at: string; symbol: string; trading_date: string; session: Session; interval: string; interval_name: string; bars: ReplayBar[]; strategies: ReplayStrategy[] };
type StrategyOption = { key: string; name: string; kind: "atomic" | "composite"; color: string };
type Availability = { date: string; sessions: { key: Session; bar_count: number }[] };
type ReplayOptions = { available_start: string | null; available_end: string | null; available_dates: Availability[]; intervals: { key: string; name: string }[]; strategies: StrategyOption[]; max_strategies: number; sessions: { key: Session; name: string }[] };

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_MARKET_API_URL?.replace(/\/$/, "");
const apiBase = () => CONFIGURED_API_BASE || (typeof window === "undefined" ? "http://localhost:8000" : window.location.origin);
const asTime = (value: string) => Math.floor(Date.parse(value) / 1000) as UTCTimestamp;
const fmtPrice = (value?: number | null) => value == null ? "—" : new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 }).format(value);
const fmtClock = (value?: string) => value ? new Date(value).toLocaleTimeString("zh-TW", { timeZone: "Asia/Taipei", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
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
    markerRef.current?.setMarkers(snapshot.strategies.flatMap(strategy => strategy.signals.filter(signal => Date.parse(signal.time) <= now).map(signal => {
      const signalAt = Date.parse(signal.time);
      const anchor = [...bars].reverse().find(bar => Date.parse(bar.time) <= signalAt) ?? bars[0];
      return marker(strategy, signal, anchor.time);
    })).sort((a, b) => Number(a.time) - Number(b.time)));
    if (fit) chartRef.current?.timeScale().fitContent(); else chartRef.current?.timeScale().scrollToRealTime();
  }, [snapshot]);

  useEffect(() => { paint(cursor); }, [cursor, paint]);
  useEffect(() => {
    if (!playing || !snapshot) return;
    const timer = window.setInterval(() => setCursor(value => { if (value >= snapshot.bars.length - 1) { setPlaying(false); return value; } return value + 1; }), Math.max(50, 1000 / speed));
    return () => window.clearInterval(timer);
  }, [playing, snapshot, speed]);

  const prepare = async () => {
    if (!date || !selected.length) return;
    setLoading(true); setError(""); setPlaying(false);
    try {
      const response = await fetch(`${apiBase()}/api/replay/prepare`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: "TMF", trading_date: date, session, interval, strategies: selected }) });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? "回放快照建立失敗");
      setSnapshot(body); setCursor(0);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "回放快照建立失敗"); }
    finally { setLoading(false); }
  };

  const toggleStrategy = (key: string) => setSelected(current => current.includes(key) ? current.filter(item => item !== key) : current.length < (options?.max_strategies ?? 3) ? [...current, key] : current);
  const current = snapshot?.bars[cursor];
  const progress = snapshot?.bars.length ? Math.round((cursor + 1) / snapshot.bars.length * 100) : 0;
  const visibleSignals = snapshot?.strategies.map(strategy => ({ ...strategy, visible: strategy.signals.filter(signal => !current || Date.parse(signal.time) <= Date.parse(current.end_time)) })) ?? [];

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
      <section className="replay-lower">
        <div className="replay-strategy-status panel"><header><span>STRATEGY EVENTS</span><h2>截至目前的策略訊號</h2></header>{visibleSignals.map(strategy => { const latest = strategy.visible[strategy.visible.length - 1]; return <article key={strategy.key}><div><i style={{ background: strategy.color }} /><strong>{strategy.name}</strong><small>{strategy.visible.length} 個訊號</small></div>{latest ? <dl><div><dt>狀態</dt><dd>{latest.event === "entry" ? latest.direction === "long" ? "多單進場" : "空單進場" : "已出場"}</dd></div><div><dt>時間</dt><dd>{fmtClock(latest.time)}</dd></div><div><dt>價格</dt><dd>{fmtPrice(latest.price)}</dd></div><div><dt>停損 / 停利</dt><dd>{fmtPrice(latest.stop_loss_price)} / {fmtPrice(latest.take_profit_price)}</dd></div></dl> : <p>時間軸尚未出現訊號</p>}</article>; })}</div>
        <aside className="replay-now panel"><span>NOW PLAYING</span><h2>{fmtClock(current?.time)}</h2><dl><div><dt>開</dt><dd>{fmtPrice(current?.open)}</dd></div><div><dt>高</dt><dd>{fmtPrice(current?.high)}</dd></div><div><dt>低</dt><dd>{fmtPrice(current?.low)}</dd></div><div><dt>收</dt><dd>{fmtPrice(current?.close)}</dd></div><div><dt>量</dt><dd>{fmtPrice(current?.volume)}</dd></div><div><dt>合約</dt><dd>{current?.contract ?? "—"}</dd></div></dl><p>這是建立當下的唯讀快照，不會影響即時行情或其他使用者。</p></aside>
      </section>
    </>}
  </>;
}
