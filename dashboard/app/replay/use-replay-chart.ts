import { useCallback, useEffect, useRef } from "react";
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
import { formatPrice, formatTaipeiClock } from "../lib/formatters";
import type {
  ReplaySignal,
  ReplaySnapshot,
  ReplayStrategy,
  ReplayTradingState,
} from "./replay-dashboard";

const asTime = (value: string) => Math.floor(Date.parse(value) / 1000) as UTCTimestamp;

function chartClock(value: Time): string {
  if (typeof value === "object") {
    return formatTaipeiClock(Date.UTC(value.year, value.month - 1, value.day) / 1000);
  }
  return formatTaipeiClock(value);
}

function marker(
  strategy: ReplayStrategy,
  signal: ReplaySignal,
  time = signal.time,
): SeriesMarker<Time> {
  const entry = signal.event === "entry";
  const long = signal.direction === "long";
  return {
    time: asTime(time),
    position: long ? entry ? "belowBar" : "aboveBar" : entry ? "aboveBar" : "belowBar",
    color: entry ? strategy.color : "#f5b942",
    shape: entry ? long ? "arrowUp" : "arrowDown" : "circle",
    text: `${strategy.name} · ${entry ? long ? "多進" : "空進" : "出場"} ${formatPrice(signal.price)}`,
  };
}

type ReplayChartOptions = {
  snapshot: ReplaySnapshot | null;
  trading: ReplayTradingState | null;
  cursor: number;
};

export function useReplayChart({ snapshot, trading, cursor }: ReplayChartOptions) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markerRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = createChart(hostRef.current, {
      width: hostRef.current.clientWidth,
      height: 560,
      layout: {
        background: { type: ColorType.Solid, color: "#07111f" },
        textColor: "#9fb0c7",
        panes: { separatorColor: "#17283b" },
        attributionLogo: true,
      },
      grid: { vertLines: { color: "#132237" }, horzLines: { color: "#132237" } },
      timeScale: {
        borderColor: "#26384d",
        timeVisible: true,
        secondsVisible: false,
        rightOffset: 8,
        tickMarkFormatter: chartClock,
      },
      rightPriceScale: { borderColor: "#26384d" },
      localization: { locale: "zh-TW", timeFormatter: chartClock },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#2dd4bf",
      downColor: "#f87171",
      borderVisible: false,
      wickUpColor: "#2dd4bf",
      wickDownColor: "#f87171",
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    }, 0);
    const volumes = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    }, 1);
    chart.panes()[1]?.setHeight(110);
    chartRef.current = chart;
    candleRef.current = candles;
    volumeRef.current = volumes;
    markerRef.current = createSeriesMarkers(candles, []);
    const observer = new ResizeObserver(entries => {
      const width = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (width > 0) {
        chart.applyOptions({ width, height: window.innerWidth < 700 ? 460 : 560 });
      }
    });
    observer.observe(hostRef.current);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      markerRef.current = null;
    };
  }, [snapshot]);

  const paint = useCallback((nextCursor: number, fit = false) => {
    if (!snapshot?.bars.length) return;
    const count = Math.max(1, Math.min(nextCursor + 1, snapshot.bars.length));
    const bars = snapshot.bars.slice(0, count);
    candleRef.current?.setData(bars.map(bar => ({
      time: asTime(bar.time),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    } as CandlestickData<UTCTimestamp>)));
    volumeRef.current?.setData(bars.map(bar => ({
      time: asTime(bar.time),
      value: bar.volume,
      color: bar.no_trade
        ? "rgba(148,163,184,.3)"
        : bar.close >= bar.open ? "rgba(45,212,191,.45)" : "rgba(248,113,113,.45)",
    } as HistogramData<UTCTimestamp>)));
    const now = Date.parse(bars[bars.length - 1].end_time);
    const strategyMarkers = snapshot.strategies.flatMap(strategy => (
      strategy.signals
        .filter(signal => Date.parse(signal.time) <= now)
        .map(signal => {
          const signalAt = Date.parse(signal.time);
          const anchor = [...bars].reverse().find(bar => Date.parse(bar.time) <= signalAt) ?? bars[0];
          return marker(strategy, signal, anchor.time);
        })
    ));
    const fillMarkers: SeriesMarker<Time>[] = (trading?.fills ?? [])
      .filter(fill => Date.parse(fill.meta.occurred_at) <= now)
      .map(fill => {
        const fillAt = Date.parse(fill.meta.occurred_at);
        const anchor = [...bars].reverse().find(bar => Date.parse(bar.time) <= fillAt) ?? bars[0];
        return {
          time: asTime(anchor.time),
          position: fill.side === "buy" ? "belowBar" : "aboveBar",
          color: fill.side === "buy" ? "#42d6a4" : "#ff6b72",
          shape: fill.side === "buy" ? "arrowUp" : "arrowDown",
          text: `REPLAY ${fill.purpose === "entry" ? "成交" : "平倉"} ${fill.quantity}口 @ ${formatPrice(fill.price)}`,
        };
      });
    markerRef.current?.setMarkers(
      [...strategyMarkers, ...fillMarkers].sort((a, b) => Number(a.time) - Number(b.time)),
    );
    if (fit) chartRef.current?.timeScale().fitContent();
    else chartRef.current?.timeScale().scrollToRealTime();
  }, [snapshot, trading?.fills]);

  useEffect(() => {
    paint(cursor);
  }, [cursor, paint]);

  return { hostRef, paint };
}
