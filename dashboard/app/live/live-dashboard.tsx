"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import SystemNav from "../components/system-nav";

type ConnectionStatus = "connecting" | "connected" | "reconnecting" | "disconnected";
type KBar = {
  type: "kbar";
  interval: Timeframe;
  symbol: string;
  contract: string;
  exchange_time: string;
  received_time: string;
  latency_ms: number;
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  status: "forming" | "closed";
  connection_status: ConnectionStatus;
  session: "day" | "night";
  trading_date: string;
  no_trade: boolean;
};
type StatusMessage = {
  type: "status" | "heartbeat";
  symbol: string;
  contract: string;
  connection_status: ConnectionStatus;
  last_tick_time: string | null;
  latency_ms: number | null;
  queue_size: number;
  dropped_ticks: number;
};
type FeedMessage = KBar | StatusMessage;
type Ohlc = Pick<KBar, "open" | "high" | "low" | "close"> | null;
type StrategyKey = "orb" | "bnf";
type Timeframe = "1m" | "5m" | "10m" | "15m" | "30m" | "1h" | "1d" | "1w";
type StrategySignal = {
  strategy: StrategyKey;
  event: "entry" | "exit";
  direction: "long" | "short";
  time: string;
  price: number;
  stop_loss_price: number;
  take_profit_price: number;
  reason: string;
};
type StrategyResult = {
  key: StrategyKey;
  name: string;
  color: string;
  parameters: Record<string, number>;
  signals: StrategySignal[];
};

const CONFIGURED_API_BASE = process.env.NEXT_PUBLIC_MARKET_API_URL?.replace(/\/$/, "");
const apiBase = () => CONFIGURED_API_BASE
  || (typeof window === "undefined" ? "http://localhost:8000" : window.location.origin);
const toTime = (value: string): UTCTimestamp => Math.floor(Date.parse(value) / 1000) as UTCTimestamp;
const chartDate = (value: Time) => {
  if (typeof value === "number") return new Date(value * 1000);
  if (typeof value === "string") return new Date(value);
  return new Date(Date.UTC(value.year, value.month - 1, value.day));
};
const chartTimeFormatter = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const formatChartTime = (value: Time) => chartTimeFormatter.format(chartDate(value));
const fmt = (value?: number | null) => value == null ? "—" : new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 2 }).format(value);
const fmtTime = (value?: string | null) => value ? new Date(value).toLocaleString("zh-TW", { hour12: false, timeZone: "Asia/Taipei" }) : "尚未收到";
const STRATEGY_OPTIONS: { key: StrategyKey; name: string; detail: string; color: string }[] = [
  { key: "orb", name: "ORB 開盤突破", detail: "15 分鐘區間＋量能 · SL 0.6%／TP 1.2%", color: "#38bdf8" },
  { key: "bnf", name: "BNF 均值回歸", detail: "20MA／2Z＋RSI · SL 0.6%／TP 1.2%", color: "#a78bfa" },
];
const TIMEFRAME_OPTIONS: { key: Timeframe; name: string }[] = [
  { key: "1m", name: "1 分 K" }, { key: "5m", name: "5 分 K" },
  { key: "10m", name: "10 分 K" }, { key: "15m", name: "15 分 K" },
  { key: "30m", name: "30 分 K" }, { key: "1h", name: "1 小時 K" },
  { key: "1d", name: "日 K" }, { key: "1w", name: "週 K" },
];

function StrategyStatus({ strategy }: { strategy: StrategyResult }) {
  const latestEntry = [...strategy.signals].reverse().find(signal => signal.event === "entry");
  return <div className="strategy-card">
    <div className="strategy-card-title">
      <i style={{ background: strategy.color }} />
      <span>{strategy.name}</span>
      <b>{strategy.signals.length} 個訊號</b>
    </div>
    <div className="strategy-risk-levels">
      <span>進場 <b>{fmt(latestEntry?.price)}</b></span>
      <span className="stop">停損 <b>{fmt(latestEntry?.stop_loss_price)}</b></span>
      <span className="target">停利 <b>{fmt(latestEntry?.take_profit_price)}</b></span>
    </div>
  </div>;
}

function candle(bar: KBar): CandlestickData<UTCTimestamp> {
  const forming = bar.status === "forming";
  return {
    time: toTime(bar.time), open: bar.open, high: bar.high, low: bar.low, close: bar.close,
    ...(forming ? { color: "#f5b942", wickColor: "#f5b942", borderColor: "#f5b942" } : {}),
  };
}

function volume(bar: KBar): HistogramData<UTCTimestamp> {
  return {
    time: toTime(bar.time), value: bar.volume,
    color: bar.no_trade ? "rgba(148,163,184,.3)" : bar.close >= bar.open ? "rgba(45,212,191,.45)" : "rgba(248,113,113,.45)",
  };
}

export default function LiveDashboard() {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attempts = useRef(0);
  const shouldReconnect = useRef(true);
  const lastHeartbeat = useRef(0);
  const strategyRequest = useRef(0);
  const strategyLoaderRef = useRef<() => Promise<void>>(async () => undefined);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [latest, setLatest] = useState<KBar | null>(null);
  const [crosshair, setCrosshair] = useState<Ohlc>(null);
  const [lastTick, setLastTick] = useState<string | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [historyCount, setHistoryCount] = useState(0);
  const [selectedInterval, setSelectedInterval] = useState<Timeframe>("1m");
  const [selectedStrategies, setSelectedStrategies] = useState<StrategyKey[]>(["orb", "bnf"]);
  const [strategyResults, setStrategyResults] = useState<StrategyResult[]>([]);

  const loadHistory = useCallback(async () => {
    const response = await fetch(`${apiBase()}/api/kbars?symbol=TMF&interval=${selectedInterval}&limit=500`, { cache: "no-store" });
    if (!response.ok) throw new Error(`歷史 K 棒載入失敗 (${response.status})`);
    const bars: KBar[] = await response.json();
    candleRef.current?.setData(bars.map(candle));
    volumeRef.current?.setData(bars.map(volume));
    setHistoryCount(bars.length);
    if (bars.length) setLatest(bars[bars.length - 1]);
    return bars;
  }, [selectedInterval]);

  const loadStrategySignals = useCallback(async () => {
    const requestId = ++strategyRequest.current;
    if (!selectedStrategies.length) {
      markerRef.current?.setMarkers([]);
      setStrategyResults([]);
      return;
    }
    const selected = selectedStrategies.join(",");
    const response = await fetch(
      `${apiBase()}/api/strategy-signals?symbol=TMF&strategies=${selected}&interval=${selectedInterval}&limit=500`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`策略訊號載入失敗 (${response.status})`);
    const payload: { strategies: StrategyResult[] } = await response.json();
    if (requestId !== strategyRequest.current) return;
    setStrategyResults(payload.strategies);
    const markers: SeriesMarker<Time>[] = payload.strategies.flatMap(strategy =>
      strategy.signals.map(signal => ({
        time: toTime(signal.time),
        position: signal.direction === "long"
          ? signal.event === "entry" ? "belowBar" : "aboveBar"
          : signal.event === "entry" ? "aboveBar" : "belowBar",
        color: signal.event === "entry" ? strategy.color : strategy.key === "orb" ? "#f59e0b" : "#f472b6",
        shape: signal.event === "entry"
          ? signal.direction === "long" ? "arrowUp" : "arrowDown"
          : "circle",
        text: signal.event === "entry"
          ? `${strategy.key.toUpperCase()} ${signal.direction === "long" ? "多" : "空"}進 · SL ${fmt(signal.stop_loss_price)} · TP ${fmt(signal.take_profit_price)}`
          : `${strategy.key.toUpperCase()} ${signal.direction === "long" ? "多" : "空"}出`,
      })),
    );
    markerRef.current?.setMarkers(markers.sort((a, b) => Number(a.time) - Number(b.time)));
  }, [selectedStrategies, selectedInterval]);

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = createChart(hostRef.current, {
      autoSize: false,
      width: hostRef.current.clientWidth,
      height: window.matchMedia("(max-width: 840px)").matches ? 500 : 610,
      layout: { background: { type: ColorType.Solid, color: "#07111f" }, textColor: "#9fb0c7", panes: { separatorColor: "#17283b" } },
      grid: { vertLines: { color: "#132237" }, horzLines: { color: "#132237" } },
      crosshair: { vertLine: { color: "#94a3b8" }, horzLine: { color: "#94a3b8" } },
      timeScale: {
        borderColor: "#26384d", timeVisible: true, secondsVisible: false,
        rightOffset: 6, tickMarkFormatter: formatChartTime,
      },
      rightPriceScale: { borderColor: "#26384d" },
      localization: { locale: "zh-TW", timeFormatter: formatChartTime },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#2dd4bf", downColor: "#f87171", borderVisible: false,
      wickUpColor: "#2dd4bf", wickDownColor: "#f87171", priceFormat: { type: "price", precision: 0, minMove: 1 },
    }, 0);
    const volumes = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" }, priceScaleId: "" }, 1);
    chart.panes()[1]?.setHeight(130);
    chart.subscribeCrosshairMove(param => {
      const value = param.seriesData.get(candles) as CandlestickData<Time> | undefined;
      setCrosshair(value && "open" in value ? { open: value.open, high: value.high, low: value.low, close: value.close } : null);
    });
    chartRef.current = chart;
    candleRef.current = candles;
    volumeRef.current = volumes;
    markerRef.current = createSeriesMarkers(candles, []);
    const resizeObserver = new ResizeObserver(entries => {
      const width = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (width > 0) chart.applyOptions({ width });
    });
    resizeObserver.observe(hostRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      markerRef.current = null;
    };
  }, []);

  useEffect(() => {
    strategyLoaderRef.current = async () => {
      await loadStrategySignals().catch(reason => {
        setError(reason instanceof Error ? reason.message : "策略訊號載入失敗");
      });
    };
    void strategyLoaderRef.current();
  }, [loadStrategySignals]);

  useEffect(() => {
    shouldReconnect.current = true;
    const connect = async () => {
      setStatus(attempts.current ? "reconnecting" : "connecting");
      try { await loadHistory(); } catch (reason) { setError(reason instanceof Error ? reason.message : "REST 載入失敗"); }
      const wsUrl = `${apiBase().replace(/^http/, "ws")}/ws/market/TMF?interval=${selectedInterval}`;
      const socket = new WebSocket(wsUrl);
      socketRef.current = socket;
      socket.onopen = async () => {
        attempts.current = 0;
        lastHeartbeat.current = Date.now();
        setError("");
        await loadHistory().catch(() => undefined); // reconnect gap recovery
        await strategyLoaderRef.current();
      };
      socket.onmessage = event => {
        const message = JSON.parse(event.data) as FeedMessage;
        if (message.type === "heartbeat") lastHeartbeat.current = Date.now();
        setStatus(message.connection_status);
        if (message.type === "kbar") {
          candleRef.current?.update(candle(message));
          volumeRef.current?.update(volume(message));
          setLatest(message);
          setLastTick(message.exchange_time);
          setLatency(message.latency_ms);
          if (message.status === "closed") void strategyLoaderRef.current();
        } else {
          setLastTick(message.last_tick_time);
          setLatency(message.latency_ms);
        }
      };
      socket.onerror = () => setError("WebSocket 連線發生錯誤");
      socket.onclose = () => {
        if (!shouldReconnect.current) return;
        setStatus("reconnecting");
        const delay = Math.min(30_000, 1_000 * 2 ** attempts.current) + Math.random() * 300;
        attempts.current += 1;
        reconnectTimer.current = setTimeout(connect, delay);
      };
    };
    connect();
    const watchdog = setInterval(() => {
      if (lastHeartbeat.current && Date.now() - lastHeartbeat.current > 15_000) {
        setStatus("disconnected");
        socketRef.current?.close();
      }
    }, 2_500);
    return () => {
      shouldReconnect.current = false;
      clearInterval(watchdog);
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      socketRef.current?.close();
    };
  }, [loadHistory, selectedInterval]);

  const toggleStrategy = (key: StrategyKey) => {
    setSelectedStrategies(current => current.includes(key)
      ? current.filter(value => value !== key)
      : [...current, key]);
  };

  const shown = crosshair ?? latest;
  return <main className="live-shell">
    <header className="live-header">
      <div><span>WADE QUANT LAB · LIVE 01</span><h1>TMF 即時 {TIMEFRAME_OPTIONS.find(item => item.key === selectedInterval)?.name}</h1></div>
      <div className="live-header-actions">
        <label className="timeframe-select"><span>K 棒週期</span><select value={selectedInterval} onChange={event => setSelectedInterval(event.target.value as Timeframe)}>{TIMEFRAME_OPTIONS.map(option => <option key={option.key} value={option.key}>{option.name}</option>)}</select></label>
        <details className="strategy-select">
          <summary>交易策略 <b>{selectedStrategies.length}</b></summary>
          <div className="strategy-menu">
            <span>MULTI-SELECT · 訊號分開疊加</span>
            {STRATEGY_OPTIONS.map(option => <label key={option.key}>
              <input type="checkbox" checked={selectedStrategies.includes(option.key)} onChange={() => toggleStrategy(option.key)} />
              <i style={{ background: option.color }} />
              <span><b>{option.name}</b><small>{option.detail}</small></span>
            </label>)}
          </div>
        </details>
        <div className={`connection-pill ${status}`}><i />{status === "connected" ? "即時連線" : status === "reconnecting" ? "重新連線中" : status === "connecting" ? "連線中" : "行情中斷"}</div>
      </div>
    </header>
    <SystemNav active="/live/" />
    <section className="live-summary">
      <div><span>商品／契約</span><b>TMF · {latest?.contract ?? "等待行情"}</b></div>
      <div><span>交易時段</span><b>{latest?.session === "night" ? "夜盤" : latest?.session === "day" ? "日盤" : "—"}</b></div>
      <div><span>最後行情時間</span><b>{fmtTime(lastTick)}</b></div>
      <div><span>資料延遲</span><b className={latency != null && latency > 1000 ? "warn" : ""}>{fmt(latency)} ms</b></div>
      <div><span>歷史 K 棒</span><b>{historyCount} 根</b></div>
    </section>
    <section className="strategy-strip">
      <div><span>策略圖層</span><b>{selectedStrategies.length ? `${selectedStrategies.length} 套啟用` : "全部隱藏"}</b></div>
      {strategyResults.map(strategy => <StrategyStatus key={strategy.key} strategy={strategy} />)}
      <small>僅用已收盤 K 棒確認；訊號於下一根開盤成立</small>
    </section>
    <section className="live-chart-panel">
      <div className="live-toolbar">
        <div><strong>{latest?.contract ?? "TMF"}</strong><span>{TIMEFRAME_OPTIONS.find(item => item.key === selectedInterval)?.name} · Asia/Taipei · Exchange Time</span></div>
        <div className="ohlc-strip"><span>O <b>{fmt(shown?.open)}</b></span><span>H <b>{fmt(shown?.high)}</b></span><span>L <b>{fmt(shown?.low)}</b></span><span>C <b>{fmt(shown?.close)}</b></span><span>V <b>{fmt(latest?.volume)}</b></span></div>
        <div className={`bar-state ${latest?.status ?? "forming"}`}>{latest?.status === "closed" ? "已收盤" : "形成中"}</div>
      </div>
      <div ref={hostRef} className="live-chart" />
      <div className="chart-legend"><span><i className="legend-forming" />形成中 K 棒</span><span><i className="legend-closed" />已收盤 K 棒</span>{STRATEGY_OPTIONS.filter(option => selectedStrategies.includes(option.key)).map(option => <span key={option.key}><i style={{ background: option.color }} />{option.name}</span>)}</div>
    </section>
    {error && <div className="live-error">{error}；系統將以指數退避自動重連。</div>}
    <footer className="live-footer">行情模式由後端設定。Mock 資料僅供工程驗證；正式 Shioaji 模式僅訂閱行情，不含下單功能。</footer>
  </main>;
}
