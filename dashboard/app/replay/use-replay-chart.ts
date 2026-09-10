import { useCallback, useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type Time,
} from "lightweight-charts";
import { formatPrice, formatTaipeiClock } from "../lib/formatters";
import { buildReplayChartFrame } from "./replay-chart-data";
import type { ReplaySnapshot, ReplayTradingState } from "./types";

function chartClock(value: Time): string {
  if (typeof value === "object") {
    return formatTaipeiClock(Date.UTC(value.year, value.month - 1, value.day) / 1000);
  }
  return formatTaipeiClock(value);
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
    const frame = buildReplayChartFrame(snapshot, trading, nextCursor, formatPrice);
    candleRef.current?.setData(frame.candles);
    volumeRef.current?.setData(frame.volumes);
    markerRef.current?.setMarkers(frame.markers);
    if (fit) chartRef.current?.timeScale().fitContent();
    else chartRef.current?.timeScale().scrollToRealTime();
  }, [snapshot, trading]);

  useEffect(() => {
    paint(cursor);
  }, [cursor, paint]);

  return { hostRef, paint };
}
