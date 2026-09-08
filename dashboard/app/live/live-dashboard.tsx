"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type CandlestickData,
  type HistogramData,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import SystemNav from "../components/system-nav";
import PaperTradingDashboard, {
  type MarketHealth,
  type PaperOverlaySnapshot,
} from "../paper/paper-trading-dashboard";

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
type StrategyKey = string;
type SymbolKey = "TMF";
type Timeframe = "1m" | "5m" | "10m" | "15m" | "30m" | "1h" | "1d" | "1w";
type TradeSelection = {
  symbol: SymbolKey;
  interval: Timeframe;
  strategies: StrategyKey[];
};
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
type StrategyOption = {
  key: StrategyKey;
  name: string;
  category: string;
  description: string;
  color: string;
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
const TIMEFRAME_OPTIONS: { key: Timeframe; name: string }[] = [
  { key: "1m", name: "1 分 K" }, { key: "5m", name: "5 分 K" },
  { key: "10m", name: "10 分 K" }, { key: "15m", name: "15 分 K" },
  { key: "30m", name: "30 分 K" }, { key: "1h", name: "1 小時 K" },
  { key: "1d", name: "日 K" }, { key: "1w", name: "週 K" },
];
const PRODUCT_OPTIONS: { key: SymbolKey; name: string }[] = [
  { key: "TMF", name: "微型臺指期貨" },
];
const EMPTY_PAPER_OVERLAY: PaperOverlaySnapshot = {
  positions: [], orders: [], fills: [],
};

function chartBarAtOrBefore(value: string, times: UTCTimestamp[]): UTCTimestamp | null {
  const target = toTime(value);
  for (let index = times.length - 1; index >= 0; index -= 1) {
    if (times[index] <= target) return times[index];
  }
  return null;
}

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

export default function TradingWorkspace() {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const barTimesRef = useRef<UTCTimestamp[]>([]);
  const socketRef = useRef<WebSocket | null>(null);
  const connectionGeneration = useRef(0);
  const attempts = useRef(0);
  const lastMessageAt = useRef(0);
  const strategyRequest = useRef(0);
  const strategyLoaderRef = useRef<() => Promise<void>>(async () => undefined);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [latest, setLatest] = useState<KBar | null>(null);
  const [crosshair, setCrosshair] = useState<Ohlc>(null);
  const [lastTick, setLastTick] = useState<string | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [historyCount, setHistoryCount] = useState(0);
  const [selection, setSelection] = useState<TradeSelection>({
    symbol: "TMF", interval: "1m", strategies: ["orb", "bnf"],
  });
  const [strategyOptions, setStrategyOptions] = useState<StrategyOption[]>([]);
  const [strategyResults, setStrategyResults] = useState<StrategyResult[]>([]);
  const [marketHealth, setMarketHealth] = useState<MarketHealth | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [paperOverlay, setPaperOverlay] = useState<PaperOverlaySnapshot>(EMPTY_PAPER_OVERLAY);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const selectedInterval = selection.interval;
  const selectedStrategies = selection.strategies;
  const updatePaperOverlay = useCallback((snapshot: PaperOverlaySnapshot) => {
    setPaperOverlay(snapshot);
  }, []);

  useEffect(() => {
    if (!settingsOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSettingsOpen(false);
    };
    document.body.style.overflow = "hidden";
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [settingsOpen]);

  useEffect(() => {
    let active = true;
    fetch(`${apiBase()}/api/strategies`, { cache: "no-store" })
      .then(async response => {
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? "策略清單載入失敗");
        if (active) setStrategyOptions(body.strategies);
      })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : "策略清單載入失敗"); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    const loadHealth = async () => {
      try {
        const response = await fetch(`${apiBase()}/api/health`, { cache: "no-store" });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail ?? `健康狀態載入失敗 (${response.status})`);
        if (active) setMarketHealth(body);
      } catch {
        if (active) setMarketHealth(null);
      }
    };
    void loadHealth();
    const healthTimer = window.setInterval(() => void loadHealth(), 5_000);
    const clockTimer = window.setInterval(() => setClock(Date.now()), 1_000);
    return () => {
      active = false;
      window.clearInterval(healthTimer);
      window.clearInterval(clockTimer);
    };
  }, []);

  const loadHistory = useCallback(async (symbol: SymbolKey, interval: Timeframe, signal?: AbortSignal) => {
    const response = await fetch(`${apiBase()}/api/kbars?symbol=${symbol}&interval=${interval}&limit=500`, { cache: "no-store", signal });
    if (!response.ok) throw new Error(`歷史 K 棒載入失敗 (${response.status})`);
    const bars: KBar[] = await response.json();
    if (signal?.aborted) return [];
    if (bars.some(bar => bar.interval !== interval)) {
      throw new Error(`歷史 K 棒週期不符（預期 ${interval}）`);
    }
    candleRef.current?.setData(bars.map(candle));
    volumeRef.current?.setData(bars.map(volume));
    barTimesRef.current = bars.map(bar => toTime(bar.time));
    setHistoryCount(bars.length);
    if (bars.length) setLatest(bars[bars.length - 1]);
    return bars;
  }, []);

  const loadStrategySignals = useCallback(async () => {
    const requestId = ++strategyRequest.current;
    if (!selectedStrategies.length) {
      markerRef.current?.setMarkers([]);
      setStrategyResults([]);
      return;
    }
    const selected = selectedStrategies.join(",");
    const response = await fetch(
      `${apiBase()}/api/strategy-signals?symbol=${selection.symbol}&strategies=${selected}&interval=${selectedInterval}&limit=500`,
      { cache: "no-store" },
    );
    if (!response.ok) throw new Error(`策略訊號載入失敗 (${response.status})`);
    const payload: { strategies: StrategyResult[] } = await response.json();
    if (requestId !== strategyRequest.current) return;
    setStrategyResults(payload.strategies);
  }, [selectedStrategies, selectedInterval, selection.symbol]);

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
      priceLinesRef.current = [];
    };
  }, []);

  useEffect(() => {
    const strategyMarkers: SeriesMarker<Time>[] = strategyResults.flatMap(strategy =>
      strategy.signals.map(signal => ({
        time: toTime(signal.time),
        position: signal.direction === "long"
          ? signal.event === "entry" ? "belowBar" as const : "aboveBar" as const
          : signal.event === "entry" ? "aboveBar" as const : "belowBar" as const,
        color: signal.event === "entry" ? strategy.color : "#f59e0b",
        shape: signal.event === "entry"
          ? signal.direction === "long" ? "arrowUp" as const : "arrowDown" as const
          : "circle" as const,
        text: signal.event === "entry"
          ? `${strategy.key.toUpperCase()} ${signal.direction === "long" ? "多" : "空"}進 · SL ${fmt(signal.stop_loss_price)} · TP ${fmt(signal.take_profit_price)}`
          : `${strategy.key.toUpperCase()} ${signal.direction === "long" ? "多" : "空"}出`,
      })),
    );
    const fillMarkers: SeriesMarker<Time>[] = paperOverlay.fills.flatMap(fill => {
      if (fill.symbol !== selection.symbol || (latest?.contract && fill.contract !== latest.contract)) return [];
      const markerTime = chartBarAtOrBefore(fill.meta.occurred_at, barTimesRef.current);
      if (markerTime == null) return [];
      return [{
        time: markerTime,
        position: fill.side === "buy" ? "belowBar" as const : "aboveBar" as const,
        color: fill.side === "buy" ? "#42d6a4" : "#ff6b72",
        shape: fill.side === "buy" ? "arrowUp" as const : "arrowDown" as const,
        text: `PAPER ${fill.purpose === "entry" ? "成交" : "平倉"} ${fill.quantity}口 @ ${fmt(fill.price)}`,
      }];
    });
    markerRef.current?.setMarkers(
      [...strategyMarkers, ...fillMarkers].sort((a, b) => Number(a.time) - Number(b.time)),
    );
  }, [historyCount, latest?.contract, latest?.time, paperOverlay.fills, selectedInterval, selection.symbol, strategyResults]);

  useEffect(() => {
    const series = candleRef.current;
    if (!series) return;
    priceLinesRef.current.forEach(line => series.removePriceLine(line));
    priceLinesRef.current = [];
    const positions = paperOverlay.positions.filter(position =>
      position.symbol === selection.symbol
      && (!latest?.contract || position.contract === latest.contract),
    );
    for (const position of positions) {
      priceLinesRef.current.push(series.createPriceLine({
        price: position.average_price,
        color: position.quantity > 0 ? "#42d6a4" : "#ff6b72",
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: `PAPER ${position.quantity > 0 ? "多" : "空"}均價 ${Math.abs(position.quantity)}口`,
      }));
      const entryOrder = paperOverlay.orders.find(order =>
        order.status === "filled"
        && !order.reduce_only
        && order.strategy_id === position.strategy_id
        && order.strategy_version === position.strategy_version
        && order.contract === position.contract
        && order.stop_loss_price != null,
      );
      if (entryOrder?.stop_loss_price != null) {
        priceLinesRef.current.push(series.createPriceLine({
          price: entryOrder.stop_loss_price,
          color: "#ff6b72",
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: "PAPER 停損",
        }));
      }
    }
  }, [latest?.contract, paperOverlay.orders, paperOverlay.positions, selection.symbol]);

  useEffect(() => {
    strategyLoaderRef.current = async () => {
      await loadStrategySignals().catch(reason => {
        setError(reason instanceof Error ? reason.message : "策略訊號載入失敗");
      });
    };
    void strategyLoaderRef.current();
  }, [loadStrategySignals]);

  useEffect(() => {
    const symbol = selection.symbol;
    const interval = selectedInterval;
    const generation = ++connectionGeneration.current;
    const abortController = new AbortController();
    let disposed = false;
    let activeSocket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    const isCurrentGeneration = () => (
      !disposed && connectionGeneration.current === generation
    );
    const isCurrentSocket = (socket: WebSocket) => (
      isCurrentGeneration()
      && activeSocket === socket
      && socketRef.current === socket
    );

    attempts.current = 0;
    lastMessageAt.current = 0;
    let initialConnect = true;
    const connect = async () => {
      if (!isCurrentGeneration()) return;
      if (initialConnect) {
        initialConnect = false;
        setLatest(null);
        setCrosshair(null);
        setHistoryCount(0);
        setStrategyResults([]);
        barTimesRef.current = [];
        candleRef.current?.setData([]);
        volumeRef.current?.setData([]);
        markerRef.current?.setMarkers([]);
      }
      setStatus(attempts.current ? "reconnecting" : "connecting");
      try {
        await loadHistory(symbol, interval, abortController.signal);
      } catch (reason) {
        if (isCurrentGeneration() && !(reason instanceof DOMException && reason.name === "AbortError")) {
          setError(reason instanceof Error ? reason.message : "REST 載入失敗");
        }
      }
      if (!isCurrentGeneration()) return;
      const wsUrl = `${apiBase().replace(/^http/, "ws")}/ws/market/${symbol}?interval=${interval}`;
      const socket = new WebSocket(wsUrl);
      activeSocket = socket;
      socketRef.current = socket;
      socket.onopen = async () => {
        if (!isCurrentSocket(socket)) return;
        attempts.current = 0;
        lastMessageAt.current = Date.now();
        setError("");
        await loadHistory(symbol, interval, abortController.signal).catch(() => undefined); // reconnect gap recovery
        if (!isCurrentSocket(socket)) return;
        await strategyLoaderRef.current();
      };
      socket.onmessage = event => {
        if (!isCurrentSocket(socket)) return;
        const message = JSON.parse(event.data) as FeedMessage;
        lastMessageAt.current = Date.now();
        setStatus(message.connection_status);
        if (message.type === "kbar") {
          if (message.interval !== interval) return;
          candleRef.current?.update(candle(message));
          volumeRef.current?.update(volume(message));
          const messageTime = toTime(message.time);
          if (barTimesRef.current.at(-1) !== messageTime) barTimesRef.current.push(messageTime);
          setLatest(message);
          setLastTick(message.exchange_time);
          setLatency(message.latency_ms);
          if (message.status === "closed") void strategyLoaderRef.current();
        } else {
          setLastTick(message.last_tick_time);
          setLatency(message.latency_ms);
        }
      };
      socket.onerror = () => {
        if (isCurrentSocket(socket)) setError("WebSocket 連線發生錯誤");
      };
      socket.onclose = () => {
        if (!isCurrentSocket(socket)) return;
        socketRef.current = null;
        activeSocket = null;
        setStatus("reconnecting");
        const delay = Math.min(30_000, 1_000 * 2 ** attempts.current) + Math.random() * 300;
        attempts.current += 1;
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null;
          void connect();
        }, delay);
      };
    };
    void connect();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible" && isCurrentGeneration()) {
        lastMessageAt.current = Date.now();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    const watchdog = setInterval(() => {
      if (
        isCurrentGeneration()
        && document.visibilityState === "visible"
        && activeSocket?.readyState === WebSocket.OPEN
        && lastMessageAt.current
        && Date.now() - lastMessageAt.current > 45_000
      ) {
        setStatus("disconnected");
        activeSocket?.close();
      }
    }, 2_500);
    return () => {
      disposed = true;
      abortController.abort();
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      clearInterval(watchdog);
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socketRef.current === activeSocket) socketRef.current = null;
      activeSocket?.close();
      activeSocket = null;
    };
  }, [loadHistory, selectedInterval, selection.symbol]);

  const toggleStrategy = (key: StrategyKey) => {
    setSelection(current => ({
      ...current,
      strategies: current.strategies.includes(key)
        ? current.strategies.filter(value => value !== key)
        : [...current.strategies, key],
    }));
  };

  const shown = crosshair ?? latest;
  const quoteAgeSeconds = latest
    ? Math.max(0, Math.floor((clock - new Date(latest.received_time).getTime()) / 1_000))
    : null;
  const quoteFresh = Boolean(
    latest
    && status === "connected"
    && marketHealth?.trading_block_reason == null
    && quoteAgeSeconds != null
    && quoteAgeSeconds <= (marketHealth?.stale_after_seconds ?? 30),
  );
  return <main className="live-shell">
    <header className="live-header">
      <div><span>MILESPAPA QUANT LAB · TRADE WORKSPACE</span><h1>{selection.symbol} 交易工作台</h1></div>
      <button className="mobile-trade-settings-trigger" type="button" aria-haspopup="dialog" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(true)}>交易設定</button>
      <div className="live-header-actions">
        <label className="timeframe-select"><span>商品</span><select value={selection.symbol} onChange={event => setSelection(current => ({ ...current, symbol: event.target.value as SymbolKey }))}>{PRODUCT_OPTIONS.map(option => <option key={option.key} value={option.key}>{option.key} · {option.name}</option>)}</select></label>
        <label className="timeframe-select"><span>K 棒週期</span><select value={selectedInterval} onChange={event => setSelection(current => ({ ...current, interval: event.target.value as Timeframe }))}>{TIMEFRAME_OPTIONS.map(option => <option key={option.key} value={option.key}>{option.name}</option>)}</select></label>
        <details className="strategy-select">
          <summary>交易策略 <b>{selectedStrategies.length}</b></summary>
          <div className="strategy-menu">
            <span>MULTI-SELECT · 訊號分開疊加</span>
            {strategyOptions.map(option => <label key={option.key}>
              <input type="checkbox" checked={selectedStrategies.includes(option.key)} onChange={() => toggleStrategy(option.key)} />
              <i style={{ background: option.color }} />
              <span><b>{option.name}</b><small>{option.category} · {option.description}</small></span>
            </label>)}
          </div>
        </details>
        <div className="paper-mode-pill">PAPER</div>
        <div className={`freshness-pill ${quoteFresh ? "fresh" : "stale"}`}>報價 {quoteAgeSeconds == null ? "等待中" : `${quoteAgeSeconds} 秒前`}</div>
        <div className={`connection-pill ${status}`}><i />{status === "connected" ? "即時連線" : status === "reconnecting" ? "重新連線中" : status === "connecting" ? "連線中" : "行情中斷"}</div>
      </div>
    </header>
    {settingsOpen && <>
      <button className="trade-settings-backdrop" type="button" aria-label="關閉交易設定" onClick={() => setSettingsOpen(false)} />
      <section className="trade-settings-sheet" role="dialog" aria-modal="true" aria-labelledby="trade-settings-title">
        <header>
          <div><span>TRADING SETTINGS</span><h2 id="trade-settings-title">交易設定</h2></div>
          <button type="button" aria-label="關閉交易設定" onClick={() => setSettingsOpen(false)}>×</button>
        </header>
        <div className="trade-settings-grid">
          <label><span>商品</span><select value={selection.symbol} onChange={event => setSelection(current => ({ ...current, symbol: event.target.value as SymbolKey }))}>{PRODUCT_OPTIONS.map(option => <option key={option.key} value={option.key}>{option.key} · {option.name}</option>)}</select></label>
          <label><span>K 棒週期</span><select value={selectedInterval} onChange={event => setSelection(current => ({ ...current, interval: event.target.value as Timeframe }))}>{TIMEFRAME_OPTIONS.map(option => <option key={option.key} value={option.key}>{option.name}</option>)}</select></label>
        </div>
        <fieldset className="trade-settings-strategies">
          <legend>交易策略 · 已啟用 {selectedStrategies.length} 套</legend>
          {strategyOptions.map(option => <label key={option.key}>
            <input type="checkbox" checked={selectedStrategies.includes(option.key)} onChange={() => toggleStrategy(option.key)} />
            <i style={{ background: option.color }} />
            <span><b>{option.name}</b><small>{option.category} · {option.description}</small></span>
          </label>)}
        </fieldset>
        <div className="trade-settings-status">
          <div className="paper-mode-pill">PAPER</div>
          <div className={`freshness-pill ${quoteFresh ? "fresh" : "stale"}`}>報價 {quoteAgeSeconds == null ? "等待中" : `${quoteAgeSeconds} 秒前`}</div>
          <div className={`connection-pill ${status}`}><i />{status === "connected" ? "即時連線" : status === "reconnecting" ? "重新連線中" : status === "connecting" ? "連線中" : "行情中斷"}</div>
        </div>
        <button className="trade-settings-done" type="button" onClick={() => setSettingsOpen(false)}>完成</button>
      </section>
    </>}
    <SystemNav active="/trade/" />
    <section className="live-summary">
      <div><span>商品／契約</span><b>{selection.symbol} · {latest?.contract ?? "等待行情"}</b></div>
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
    <PaperTradingDashboard
      quote={latest}
      quoteFresh={quoteFresh}
      marketHealth={marketHealth}
      onOverlayChange={updatePaperOverlay}
      marketPanel={<>
        <section className="live-chart-panel">
          <div className="live-toolbar">
            <div><strong>{latest?.contract ?? selection.symbol}</strong><span>{TIMEFRAME_OPTIONS.find(item => item.key === selectedInterval)?.name} · Asia/Taipei · Exchange Time</span></div>
            <div className="ohlc-strip"><span>O <b>{fmt(shown?.open)}</b></span><span>H <b>{fmt(shown?.high)}</b></span><span>L <b>{fmt(shown?.low)}</b></span><span>C <b>{fmt(shown?.close)}</b></span><span>V <b>{fmt(latest?.volume)}</b></span></div>
            <div className={`bar-state ${latest?.status ?? "forming"}`}>{latest?.status === "closed" ? "已收盤" : "形成中"}</div>
          </div>
          <div ref={hostRef} className="live-chart" />
          <div className="chart-legend"><span><i className="legend-forming" />形成中 K 棒</span><span><i className="legend-closed" />已收盤 K 棒</span>{paperOverlay.fills.length > 0 && <span><i className="legend-paper-fill" />Paper 成交</span>}{paperOverlay.positions.length > 0 && <><span><i className="legend-position" />持倉均價</span><span><i className="legend-stop-line" />停損</span></>}{strategyOptions.filter(option => selectedStrategies.includes(option.key)).map(option => <span key={option.key}><i style={{ background: option.color }} />{option.name}</span>)}</div>
        </section>
        {error && <div className="live-error">{error}；系統將以指數退避自動重連。</div>}
      </>}
    />
    <footer className="live-footer">行情模式由後端設定。Mock 資料僅供工程驗證；正式 Shioaji 模式僅訂閱行情，不含下單功能。</footer>
  </main>;
}
