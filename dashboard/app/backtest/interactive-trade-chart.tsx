"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { formatPrice, formatTaipeiClock } from "../lib/formatters";
import {
  backtestChartTime,
  tradeFocusRange,
  type BacktestBar,
  type BacktestTrade,
  type StrategyOverlay,
} from "./trade-chart-model";

const taipeiDate = new Intl.DateTimeFormat("zh-TW", {
  timeZone: "Asia/Taipei",
  month: "2-digit",
  day: "2-digit",
});

function chartTime(value: string): UTCTimestamp {
  return Math.floor(Date.parse(value) / 1000) as UTCTimestamp;
}

function barTime(bar: BacktestBar, rangeMode: boolean): UTCTimestamp {
  return backtestChartTime(bar, rangeMode) as UTCTimestamp;
}

function clock(value: Time, rangeMode: boolean): string {
  if (typeof value === "object") {
    const epoch = Date.UTC(value.year, value.month - 1, value.day) / 1000;
    return rangeMode ? taipeiDate.format(epoch * 1000) : formatTaipeiClock(epoch);
  }
  const epoch = typeof value === "number" ? value : Date.parse(value) / 1000;
  return rangeMode ? taipeiDate.format(epoch * 1000) : formatTaipeiClock(epoch);
}

function anchorTime(
  bars: BacktestBar[],
  timestamp: string,
  rangeMode: boolean,
): UTCTimestamp {
  const target = Date.parse(timestamp);
  const bar = [...bars].reverse().find(item => Date.parse(item.timestamp) <= target)
    ?? bars[0];
  return barTime(bar, rangeMode);
}

function tradeMarkers(
  bars: BacktestBar[],
  trades: BacktestTrade[],
  selected: BacktestTrade,
  rangeMode: boolean,
): SeriesMarker<Time>[] {
  return trades.flatMap((trade, index) => {
    const active = trade.entry_time === selected.entry_time
      && trade.exit_time === selected.exit_time;
    const number = (trade.trade_index ?? index) + 1;
    const entryColor = active ? "#42d6a4" : "rgba(66,214,164,.5)";
    const exitColor = active ? "#f5b942" : "rgba(245,185,66,.5)";
    return [
      {
        time: anchorTime(bars, trade.entry_time, rangeMode),
        position: trade.direction === "long" ? "belowBar" : "aboveBar",
        color: entryColor,
        shape: trade.direction === "long" ? "arrowUp" : "arrowDown",
        text: active ? `#${number} 進 ${formatPrice(trade.entry_price)}` : `#${number} 進`,
      },
      {
        time: anchorTime(bars, trade.exit_time, rangeMode),
        position: trade.direction === "long" ? "aboveBar" : "belowBar",
        color: exitColor,
        shape: "circle",
        text: active ? `#${number} 出 ${formatPrice(trade.exit_price)}` : `#${number} 出`,
      },
    ] as SeriesMarker<Time>[];
  }).sort((left, right) => Number(left.time) - Number(right.time));
}

type InteractiveTradeChartProps = {
  bars: BacktestBar[];
  trades: BacktestTrade[];
  selectedTrade: BacktestTrade;
  overlays?: StrategyOverlay[];
  rangeMode?: boolean;
  focusSelection?: boolean;
};

export default function InteractiveTradeChart({
  bars,
  trades,
  selectedTrade,
  overlays = [],
  rangeMode = false,
  focusSelection = true,
}: InteractiveTradeChartProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const overlayRefs = useRef<ISeriesApi<"Line">[]>([]);
  const riskRefs = useRef<IPriceLine[]>([]);
  const selectedKey = `${selectedTrade.entry_time}:${selectedTrade.exit_time}`;
  const barRevision = useMemo(
    () => `${bars.length}:${bars[0]?.timestamp ?? ""}:${bars.at(-1)?.timestamp ?? ""}`,
    [bars],
  );

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = createChart(hostRef.current, {
      width: hostRef.current.clientWidth,
      height: 460,
      layout: {
        background: { type: ColorType.Solid, color: "#07120f" },
        textColor: "#9fb0c7",
        attributionLogo: true,
        panes: { separatorColor: "#173027" },
      },
      grid: { vertLines: { color: "#13271f" }, horzLines: { color: "#13271f" } },
      rightPriceScale: { borderColor: "#29463b" },
      timeScale: {
        borderColor: "#29463b",
        timeVisible: !rangeMode,
        secondsVisible: false,
        rightOffset: 4,
        tickMarkFormatter: (value: Time) => clock(value, rangeMode),
      },
      localization: {
        locale: "zh-TW",
        timeFormatter: (value: Time) => clock(value, rangeMode),
      },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#42d6a4",
      downColor: "#ff6b72",
      wickUpColor: "#42d6a4",
      wickDownColor: "#ff6b72",
      borderVisible: false,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    }, 0);
    const volumes = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "",
    }, 1);
    chart.panes()[1]?.setHeight(90);
    chartRef.current = chart;
    candleRef.current = candles;
    volumeRef.current = volumes;
    markersRef.current = createSeriesMarkers(candles, []);
    const observer = new ResizeObserver(entries => {
      const width = Math.floor(entries[0]?.contentRect.width ?? 0);
      if (width > 0) chart.applyOptions({ width, height: window.innerWidth < 700 ? 400 : 460 });
    });
    observer.observe(hostRef.current);
    return () => {
      observer.disconnect();
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      markersRef.current = null;
      overlayRefs.current = [];
      riskRefs.current = [];
    };
  }, [rangeMode]);

  useEffect(() => {
    const chart = chartRef.current;
    const candles = candleRef.current;
    if (!chart || !candles || !bars.length) return;
    candles.setData(bars.map(bar => ({
      time: barTime(bar, rangeMode),
      open: bar.open,
      high: bar.high,
      low: bar.low,
      close: bar.close,
    })));
    volumeRef.current?.setData(bars.map(bar => ({
      time: barTime(bar, rangeMode),
      value: bar.volume,
      color: bar.close >= bar.open ? "rgba(66,214,164,.4)" : "rgba(255,107,114,.4)",
    })));
    overlayRefs.current.forEach(series => chart.removeSeries(series));
    overlayRefs.current = [];
    const chartTimes = new Map(
      bars.map(bar => [bar.timestamp.slice(0, 16), barTime(bar, rangeMode)]),
    );
    overlays.filter(item => item.type === "linear_channel").forEach(overlay => {
      const groups = new Map<string, typeof overlay.points>();
      overlay.points
        .filter(point => chartTimes.has(point.time.slice(0, 16)))
        .forEach(point => {
          const key = point.channel_id ?? "channel";
          groups.set(key, [...(groups.get(key) ?? []), point]);
        });
      groups.forEach(points => {
        (["upper", "center", "lower"] as const).forEach(key => {
          const series = chart.addSeries(LineSeries, {
            color: key === "center" ? "rgba(167,139,250,.45)" : "rgba(167,139,250,.8)",
            lineWidth: key === "center" ? 1 : 2,
            priceLineVisible: false,
            lastValueVisible: false,
          }, 0);
          series.setData(points.map(point => ({
            time: chartTimes.get(point.time.slice(0, 16)) ?? chartTime(point.time),
            value: point[key],
          })));
          overlayRefs.current.push(series);
        });
      });
    });
    chart.timeScale().fitContent();
  }, [barRevision, bars, overlays, rangeMode]);

  useEffect(() => {
    const candles = candleRef.current;
    if (!candles || !bars.length) return;
    markersRef.current?.setMarkers(tradeMarkers(bars, trades, selectedTrade, rangeMode));
    riskRefs.current.forEach(line => candles.removePriceLine(line));
    riskRefs.current = [];
    if (selectedTrade.stop_loss_price != null) {
      riskRefs.current.push(candles.createPriceLine({
        price: selectedTrade.stop_loss_price,
        color: "#ff6b72",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "停損",
      }));
    }
    if (selectedTrade.take_profit_price != null) {
      riskRefs.current.push(candles.createPriceLine({
        price: selectedTrade.take_profit_price,
        color: "#42d6a4",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: "停利",
      }));
    }
    const visible = tradeFocusRange(bars, selectedTrade);
    if (focusSelection && visible) {
      chartRef.current?.timeScale().setVisibleLogicalRange(visible);
    } else {
      chartRef.current?.timeScale().fitContent();
    }
  }, [bars, focusSelection, rangeMode, trades, selectedKey, selectedTrade]);

  return <div className="interactive-trade-chart">
    <div className="interactive-chart-toolbar">
      <span>滾輪／拖曳／雙指可縮放</span>
      <button type="button" onClick={() => chartRef.current?.timeScale().fitContent()}>
        顯示全時段
      </button>
    </div>
    <div ref={hostRef} className="interactive-chart-host" aria-label="互動 K 線與交易進出場位置" />
  </div>;
}
