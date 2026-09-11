from __future__ import annotations

from datetime import time
from typing import Iterable

import pandas as pd

from ..execution import SignalSimulationPolicy, simulate_signals
from ..market import KBar
from ..risk import RiskConfig
from .definitions import BNFMeanReversion, BNFMeanReversionConfig
from .parameters import (
    SUPPORTED_STRATEGIES,
    strategy_catalog,
    validate_strategy_parameters,
)
from .linear_channel import (
    DowChannelEntryMode,
    detect_linear_channels,
    dow_channel_signals,
    serialize_channel_overlay,
)
from .visualization import diagnostic_context, series, threshold, visualization


_DOW_CHANNEL_ENTRY_MODES: dict[str, DowChannelEntryMode] = {
    "dow_channel_pullback": "pullback",
    "dow_channel_reversal": "reversal",
    "linear_channel_breakout": "momentum",
}


_STRATEGY_EXIT_REASONS = {
    "bnf": "mean_reversion",
    "ma_crossover": "opposite_signal",
    "ema_trend": "opposite_signal",
    "donchian_breakout": "channel_midpoint",
    "dow_channel_pullback": "channel_invalidation",
    "dow_channel_reversal": "opposite_structure",
    "linear_channel_breakout": "channel_invalidation",
    "rsi_mean_reversion": "mean_reversion",
    "bollinger_mean_reversion": "mean_reversion",
    "macd_momentum": "opposite_signal",
    "vwap_reversion": "mean_reversion",
}


def _signal_simulation_policy(strategy: str) -> SignalSimulationPolicy:
    return SignalSimulationPolicy(
        strategy_exit_reason=_STRATEGY_EXIT_REASONS.get(strategy, "strategy_exit"),
    )


def _entry_reasons(strategy: str, entries: pd.Series) -> dict[int, str]:
    labels = {
        "orb": ("opening_range_breakout_up", "opening_range_breakout_down"),
        "bnf": ("z_score_and_rsi_oversold", "z_score_and_rsi_overbought"),
        "ma_crossover": ("short_ma_crossed_above_long_ma", "short_ma_crossed_below_long_ma"),
        "ema_trend": ("fast_ema_crossed_above_slow_ema", "fast_ema_crossed_below_slow_ema"),
        "donchian_breakout": ("close_broke_upper_channel", "close_broke_lower_channel"),
        "dow_channel_pullback": ("lower_channel_reclaimed", "upper_channel_rejected"),
        "dow_channel_reversal": ("down_channel_broken_up", "up_channel_broken_down"),
        "linear_channel_breakout": ("up_channel_momentum_breakout", "down_channel_momentum_breakout"),
        "rsi_mean_reversion": ("rsi_crossed_oversold", "rsi_crossed_overbought"),
        "bollinger_mean_reversion": ("close_touched_lower_band", "close_touched_upper_band"),
        "macd_momentum": ("macd_crossed_above_signal", "macd_crossed_below_signal"),
        "vwap_reversion": ("close_crossed_below_entry_band", "close_crossed_above_entry_band"),
        "atr_breakout": ("positive_move_exceeded_atr_threshold", "negative_move_exceeded_atr_threshold"),
        "volume_breakout": ("volume_confirmed_upper_breakout", "volume_confirmed_lower_breakout"),
    }
    long_reason, short_reason = labels[strategy]
    return {
        int(index): long_reason if int(value) == 1 else short_reason
        for index, value in entries.items()
        if int(value) in {-1, 1}
    }


def _append_visualization(
    target: dict[str, object], source: dict[str, object]
) -> None:
    current = target["visualization"]
    assert isinstance(current, dict)
    for bucket in ("overlays", "diagnostics"):
        existing = current[bucket]
        incoming = source[bucket]
        assert isinstance(existing, list) and isinstance(incoming, list)
        by_key = {str(item["key"]): item for item in existing}
        for item in incoming:
            key = str(item["key"])
            if key in by_key:
                by_key[key]["points"].extend(item["points"])
            else:
                copy = {**item, "points": list(item["points"])}
                existing.append(copy)
                by_key[key] = copy


def _dow_visualization(
    key: str,
    name: str,
    bars: pd.DataFrame,
    parameters: dict[str, int | float],
    channels: pd.DataFrame,
    legacy_overlay: dict[str, object],
) -> tuple[dict[str, object], pd.DataFrame]:
    legacy_points = legacy_overlay["points"]
    assert isinstance(legacy_points, list)
    colors = {"upper": "#a78bfa", "center": "#64748b", "lower": "#a78bfa"}
    overlays: list[dict[str, object]] = []
    for level in ("upper", "center", "lower"):
        overlays.append({
            "key": f"dow_{level}",
            "label": level.title(),
            "panel": "price",
            "type": "line",
            "color": colors[level],
            "points": [
                {
                    "time": point["time"],
                    "value": point[level],
                    "group": point.get("channel_id", "channel"),
                }
                for point in legacy_points
            ],
            "metadata": {"model": "dow_theory", "grouped": True},
        })
    atr = channels["atr"].astype(float).replace(0, float("nan"))
    close = bars["close"].astype(float)
    distance_upper = (close - channels["upper"].astype(float)) / atr
    distance_lower = (close - channels["lower"].astype(float)) / atr
    diagnostics = [
        series(bars, distance_upper, key="distance_to_upper_atr", label="Distance to Upper (ATR)", panel="strategy", color="#f59e0b"),
        series(bars, distance_lower, key="distance_to_lower_atr", label="Distance to Lower (ATR)", panel="strategy", color="#2dd4bf"),
        threshold(bars, 0, key="channel_boundary", label="Boundary"),
    ]
    context = diagnostic_context(bars, {
        "distance_to_upper_atr": distance_upper,
        "distance_to_lower_atr": distance_lower,
        "channel_upper": channels["upper"].astype(float),
        "channel_center": channels["center"].astype(float),
        "channel_lower": channels["lower"].astype(float),
    })
    return visualization(
        key, name, parameters, overlays=overlays, diagnostics=diagnostics
    ), context


def _frame(bars: Iterable[KBar]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "timestamp": bar.time,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "status": bar.status,
                "contract": bar.contract,
                "session": bar.session,
                "trading_date": bar.trading_date.isoformat(),
            }
            for bar in bars
        ]
    )


def _orb_entries(
    bars: pd.DataFrame, parameters: dict[str, int | float]
) -> tuple[pd.Series, dict[str, pd.Series]]:
    result = pd.Series(0, index=bars.index, dtype="int8")
    empty = pd.Series(float("nan"), index=bars.index)
    opening_range = int(parameters["opening_range_minutes"])
    if bars.empty:
        return result, {"upper": empty, "lower": empty, "volume_ratio": empty}
    expected_open = time(8, 45) if bars.iloc[0]["session"] == "day" else time(15, 0)
    if bars.iloc[0]["timestamp"].time().replace(tzinfo=None) != expected_open:
        return result, {"upper": empty, "lower": empty, "volume_ratio": empty}

    range_end = bars.iloc[0]["timestamp"] + pd.Timedelta(minutes=opening_range)
    opening = bars.loc[bars["timestamp"] < range_end]
    if len(opening) < 2 or not bool((bars["timestamp"] >= range_end).any()):
        return result, {"upper": empty, "lower": empty, "volume_ratio": empty}
    range_high = float(opening["high"].max())
    range_low = float(opening["low"].min())
    previous_close = bars["close"].shift(1)
    baseline_volume = (
        bars["volume"]
        .rolling(int(parameters["volume_window"]), min_periods=1)
        .mean()
        .shift(1)
    )
    closed = bars["status"] == "closed"
    eligible = (
        (bars["timestamp"] >= range_end)
        & closed
        & (bars["volume"] >= baseline_volume * float(parameters["volume_multiplier"]))
    )
    long_break = eligible & (bars["close"] > range_high) & (previous_close <= range_high)
    short_break = eligible & (bars["close"] < range_low) & (previous_close >= range_low)
    candidates = [(int(index), 1) for index in bars.index[long_break]]
    candidates += [(int(index), -1) for index in bars.index[short_break]]
    if candidates:
        index, direction = min(candidates, key=lambda item: item[0])
        result.loc[index] = direction
    upper = pd.Series(float("nan"), index=bars.index)
    lower = pd.Series(float("nan"), index=bars.index)
    visible = bars["timestamp"] >= range_end
    upper.loc[visible] = range_high
    lower.loc[visible] = range_low
    volume_ratio = bars["volume"] / baseline_volume.replace(0, float("nan"))
    return result, {"upper": upper, "lower": lower, "volume_ratio": volume_ratio}


def _bnf_signals(
    bars: pd.DataFrame, parameters: dict[str, int | float]
) -> tuple[pd.Series, pd.DataFrame, pd.DataFrame]:
    strategy = BNFMeanReversion(BNFMeanReversionConfig(
        mean_window=int(parameters["mean_window"]),
        std_window=int(parameters["std_window"]),
        entry_z_score=float(parameters["entry_z_score"]),
        exit_z_score=float(parameters["exit_z_score"]),
        rsi_period=int(parameters["rsi_period"]),
        oversold_rsi=float(parameters["oversold_rsi"]),
        overbought_rsi=float(parameters["overbought_rsi"]),
        direction="both",
    ))
    indicators = strategy.indicators(bars)
    entries = strategy.generate_entries(bars)
    exits = strategy.generate_exits(bars)
    forming = bars["status"] != "closed"
    entries.loc[forming] = 0
    exits.loc[forming, ["long", "short"]] = False
    return entries, exits, indicators


def _cross_entries(fast: pd.Series, slow: pd.Series) -> pd.Series:
    entries = pd.Series(0, index=fast.index, dtype="int8")
    entries.loc[(fast > slow) & (fast.shift(1) <= slow.shift(1))] = 1
    entries.loc[(fast < slow) & (fast.shift(1) >= slow.shift(1))] = -1
    return entries


def _opposite_exits(entries: pd.Series) -> pd.DataFrame:
    return pd.DataFrame(
        {"long": entries == -1, "short": entries == 1}, index=entries.index
    )


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).rolling(period, min_periods=period).mean()
    denominator = gain + loss
    result = 100 * gain / denominator.replace(0, float("nan"))
    return result.mask((gain == 0) & (loss == 0), 50.0)


def _dow_channel_analysis(
    key: str,
    bars: pd.DataFrame,
    parameters: dict[str, int | float],
) -> tuple[pd.Series, pd.DataFrame | None, pd.DataFrame]:
    channels = detect_linear_channels(
        bars,
        atr_period=int(parameters["atr_period"]),
        pivot_reversal_atr=float(parameters["pivot_reversal_atr"]),
        confirmation_bars=int(parameters["confirmation_bars"]),
        minimum_pivot_distance=int(parameters["minimum_pivot_distance"]),
        minimum_channel_bars=int(parameters["minimum_channel_bars"]),
        invalidation_bars=int(parameters["invalidation_bars"]),
    )
    entries, exits = dow_channel_signals(
        bars,
        channels,
        entry_mode=_DOW_CHANNEL_ENTRY_MODES[key],
        boundary_tolerance_atr=float(
            parameters.get("boundary_tolerance_atr", 0.0)
        ),
    )
    return entries, exits, channels


def _technical_signals(
    key: str, bars: pd.DataFrame, parameters: dict[str, int | float]
) -> tuple[
    pd.Series,
    pd.DataFrame | None,
    list[dict[str, object]],
    list[dict[str, object]],
    pd.DataFrame,
]:
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume"].astype(float)
    overlays: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    context_values: dict[str, pd.Series] = {}

    if key == "ma_crossover":
        fast = close.rolling(int(parameters["short_window"]), min_periods=int(parameters["short_window"])).mean()
        slow = close.rolling(int(parameters["long_window"]), min_periods=int(parameters["long_window"])).mean()
        entries = _cross_entries(fast, slow)
        exits = _opposite_exits(entries)
        overlays = [
            series(bars, fast, key="short_ma", label="Short MA", panel="price", color="#22d3ee"),
            series(bars, slow, key="long_ma", label="Long MA", panel="price", color="#f59e0b"),
        ]
        spread = fast - slow
        diagnostics = [series(bars, spread, key="ma_spread", label="MA Spread", panel="strategy", color="#a78bfa")]
        context_values = {"short_ma": fast, "long_ma": slow, "ma_spread": spread}
    elif key == "ema_trend":
        fast = close.ewm(span=int(parameters["fast_period"]), adjust=False, min_periods=int(parameters["fast_period"])).mean()
        slow = close.ewm(span=int(parameters["slow_period"]), adjust=False, min_periods=int(parameters["slow_period"])).mean()
        entries = _cross_entries(fast, slow)
        exits = _opposite_exits(entries)
        overlays = [
            series(bars, fast, key="fast_ema", label="Fast EMA", panel="price", color="#34d399"),
            series(bars, slow, key="slow_ema", label="Slow EMA", panel="price", color="#f59e0b"),
        ]
        spread = fast - slow
        diagnostics = [series(bars, spread, key="ema_spread", label="EMA Spread", panel="strategy", color="#a78bfa")]
        context_values = {"fast_ema": fast, "slow_ema": slow, "ema_spread": spread}
    elif key == "donchian_breakout":
        window = int(parameters["lookback_period"])
        upper = high.rolling(window, min_periods=window).max().shift(1)
        lower = low.rolling(window, min_periods=window).min().shift(1)
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[close > upper] = 1
        entries.loc[close < lower] = -1
        midpoint = (upper + lower) / 2
        exits = pd.DataFrame({"long": close < midpoint, "short": close > midpoint}, index=bars.index)
        overlays = [
            series(bars, upper, key="donchian_upper", label="Upper Channel", panel="price", color="#60a5fa"),
            series(bars, midpoint, key="donchian_midpoint", label="Midpoint", panel="price", color="#94a3b8"),
            series(bars, lower, key="donchian_lower", label="Lower Channel", panel="price", color="#60a5fa"),
        ]
        width = upper - lower
        diagnostics = [series(bars, width, key="channel_width", label="Channel Width", panel="strategy", color="#38bdf8")]
        context_values = {"upper_channel": upper, "lower_channel": lower, "channel_width": width}
    elif key in _DOW_CHANNEL_ENTRY_MODES:
        entries, exits, _ = _dow_channel_analysis(key, bars, parameters)
    elif key == "rsi_mean_reversion":
        indicator = _rsi(close, int(parameters["rsi_period"]))
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[(indicator <= float(parameters["oversold_rsi"])) & (indicator.shift(1) > float(parameters["oversold_rsi"]))] = 1
        entries.loc[(indicator >= float(parameters["overbought_rsi"])) & (indicator.shift(1) < float(parameters["overbought_rsi"]))] = -1
        exit_level = float(parameters["exit_rsi"])
        exits = pd.DataFrame({"long": indicator >= exit_level, "short": indicator <= exit_level}, index=bars.index)
        diagnostics = [
            series(bars, indicator, key="rsi", label="RSI", panel="strategy", color="#c084fc"),
            threshold(bars, float(parameters["oversold_rsi"]), key="rsi_oversold", label="Oversold"),
            threshold(bars, exit_level, key="rsi_exit", label="Exit", color="#f59e0b"),
            threshold(bars, float(parameters["overbought_rsi"]), key="rsi_overbought", label="Overbought"),
        ]
        context_values = {"rsi": indicator}
    elif key == "bollinger_mean_reversion":
        window = int(parameters["window"])
        mean = close.rolling(window, min_periods=window).mean()
        std = close.rolling(window, min_periods=window).std(ddof=0)
        upper = mean + std * float(parameters["std_multiplier"])
        lower = mean - std * float(parameters["std_multiplier"])
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[(std > 0) & (close <= lower)] = 1
        entries.loc[(std > 0) & (close >= upper)] = -1
        exits = pd.DataFrame({"long": close >= mean, "short": close <= mean}, index=bars.index)
        overlays = [
            series(bars, upper, key="bollinger_upper", label="Upper Band", panel="price", color="#f472b6"),
            series(bars, mean, key="bollinger_mean", label="Mean", panel="price", color="#94a3b8"),
            series(bars, lower, key="bollinger_lower", label="Lower Band", panel="price", color="#f472b6"),
        ]
        percent_b = (close - lower) / (upper - lower).replace(0, float("nan"))
        diagnostics = [
            series(bars, percent_b, key="percent_b", label="%B", panel="strategy", color="#f472b6"),
            threshold(bars, 0, key="percent_b_lower", label="Lower Band"),
            threshold(bars, 1, key="percent_b_upper", label="Upper Band"),
        ]
        context_values = {"percent_b": percent_b, "band_upper": upper, "band_mean": mean, "band_lower": lower}
    elif key == "macd_momentum":
        fast = close.ewm(span=int(parameters["fast_period"]), adjust=False, min_periods=int(parameters["fast_period"])).mean()
        slow = close.ewm(span=int(parameters["slow_period"]), adjust=False, min_periods=int(parameters["slow_period"])).mean()
        macd = fast - slow
        signal = macd.ewm(span=int(parameters["signal_period"]), adjust=False, min_periods=int(parameters["signal_period"])).mean()
        entries = _cross_entries(macd, signal)
        exits = _opposite_exits(entries)
        histogram = macd - signal
        diagnostics = [
            series(bars, macd, key="macd", label="MACD", panel="strategy", color="#f59e0b"),
            series(bars, signal, key="macd_signal", label="Signal", panel="strategy", color="#38bdf8"),
            series(bars, histogram, key="macd_histogram", label="Histogram", panel="strategy", series_type="histogram", color="#a78bfa"),
        ]
        context_values = {"macd": macd, "signal": signal, "histogram": histogram}
    elif key == "vwap_reversion":
        typical = (high + low + close) / 3
        cumulative_volume = volume.cumsum()
        vwap = (typical * volume).cumsum() / cumulative_volume.replace(0, float("nan"))
        entry_deviation = float(parameters["entry_deviation_pct"])
        lower = vwap * (1 - entry_deviation)
        upper = vwap * (1 + entry_deviation)
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[(close <= lower) & (close.shift(1) > lower.shift(1))] = 1
        entries.loc[(close >= upper) & (close.shift(1) < upper.shift(1))] = -1
        exit_deviation = float(parameters["exit_deviation_pct"])
        exits = pd.DataFrame({
            "long": close >= vwap * (1 - exit_deviation),
            "short": close <= vwap * (1 + exit_deviation),
        }, index=bars.index)
        entry_upper = vwap * (1 + entry_deviation)
        entry_lower = vwap * (1 - entry_deviation)
        exit_upper = vwap * (1 + exit_deviation)
        exit_lower = vwap * (1 - exit_deviation)
        overlays = [
            series(bars, vwap, key="vwap", label="VWAP", panel="price", color="#2dd4bf"),
            series(bars, entry_upper, key="vwap_entry_upper", label="Entry +", panel="price", color="#f59e0b"),
            series(bars, entry_lower, key="vwap_entry_lower", label="Entry -", panel="price", color="#f59e0b"),
            series(bars, exit_upper, key="vwap_exit_upper", label="Exit +", panel="price", color="#94a3b8"),
            series(bars, exit_lower, key="vwap_exit_lower", label="Exit -", panel="price", color="#94a3b8"),
        ]
        deviation = (close - vwap) / vwap.replace(0, float("nan"))
        diagnostics = [
            series(bars, deviation, key="vwap_deviation_pct", label="VWAP Deviation", panel="strategy", color="#2dd4bf", metadata={"format": "percent"}),
            threshold(bars, entry_deviation, key="entry_deviation_upper", label="Entry +"),
            threshold(bars, -entry_deviation, key="entry_deviation_lower", label="Entry -"),
            threshold(bars, exit_deviation, key="exit_deviation_upper", label="Exit +", color="#475569"),
            threshold(bars, -exit_deviation, key="exit_deviation_lower", label="Exit -", color="#475569"),
        ]
        context_values = {"vwap": vwap, "vwap_deviation_pct": deviation}
    elif key == "atr_breakout":
        previous_close = close.shift(1)
        true_range = pd.concat([
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ], axis=1).max(axis=1)
        period = int(parameters["atr_period"])
        atr = true_range.rolling(period, min_periods=period).mean().shift(1)
        distance = atr * float(parameters["atr_multiplier"])
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[close > previous_close + distance] = 1
        entries.loc[close < previous_close - distance] = -1
        exits = None
        actual_move = close - previous_close
        diagnostics = [
            series(bars, atr, key="atr", label="ATR", panel="strategy", color="#fb7185"),
            series(bars, distance, key="required_move", label="Required Move", panel="strategy", color="#f59e0b"),
            series(bars, actual_move, key="close_move", label="Close Move", panel="strategy", color="#38bdf8"),
        ]
        context_values = {"atr": atr, "required_move": distance, "close_move": actual_move}
    elif key == "volume_breakout":
        lookback = int(parameters["price_lookback"])
        upper = high.rolling(lookback, min_periods=lookback).max().shift(1)
        lower = low.rolling(lookback, min_periods=lookback).min().shift(1)
        average_volume = volume.rolling(int(parameters["volume_window"]), min_periods=int(parameters["volume_window"])).mean().shift(1)
        confirmed = volume >= average_volume * float(parameters["volume_multiplier"])
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[confirmed & (close > upper)] = 1
        entries.loc[confirmed & (close < lower)] = -1
        exits = None
        ratio = volume / average_volume.replace(0, float("nan"))
        overlays = [
            series(bars, upper, key="breakout_upper", label="Upper Breakout", panel="price", color="#a3e635"),
            series(bars, lower, key="breakout_lower", label="Lower Breakout", panel="price", color="#a3e635"),
        ]
        diagnostics = [
            series(bars, volume, key="volume", label="Volume", panel="strategy", series_type="histogram", color="#64748b", metadata={"scale_id": "volume"}),
            series(bars, average_volume, key="average_volume", label="Average Volume", panel="strategy", color="#38bdf8", metadata={"scale_id": "volume"}),
            series(bars, ratio, key="volume_ratio", label="Volume Ratio", panel="strategy", color="#a3e635", metadata={"scale_id": "ratio"}),
            threshold(bars, float(parameters["volume_multiplier"]), key="volume_threshold", label="Required Ratio", metadata={"scale_id": "ratio"}),
        ]
        context_values = {"volume": volume, "average_volume": average_volume, "volume_ratio": ratio}
    else:  # pragma: no cover - guarded by SUPPORTED_STRATEGIES
        raise ValueError(f"unsupported strategy: {key}")

    forming = bars["status"] != "closed"
    entries.loc[forming] = 0
    if exits is not None:
        exits.loc[forming, ["long", "short"]] = False
        exits = exits.fillna(False)
    return entries, exits, overlays, diagnostics, diagnostic_context(bars, context_values)


def analyze_strategies(
    bars: Iterable[KBar],
    selected: Iterable[str] = SUPPORTED_STRATEGIES,
    *,
    force_close_last: bool = False,
    parameters: dict[str, dict[str, object]] | None = None,
    interval: str = "1m",
) -> dict[str, object]:
    """Analyze canonical bars independent of whether they came from live or history."""
    requested = tuple(dict.fromkeys(value.lower() for value in selected))
    unsupported = sorted(set(requested) - set(SUPPORTED_STRATEGIES))
    if unsupported:
        raise ValueError(f"unsupported strategies: {', '.join(unsupported)}")

    frame = _frame(bars)
    resolved = {
        key: validate_strategy_parameters(key, (parameters or {}).get(key))
        for key in requested
    }
    catalog = {
        item["key"]: {
            "key": item["key"],
            "name": item["name"],
            "color": item["color"],
            "parameters": resolved[item["key"]],
            "signals": [],
            "overlays": [],
            "visualization": visualization(
                item["key"], item["name"], resolved[item["key"]]
            ),
        }
        for item in strategy_catalog(resolved)
        if item["key"] in requested
    }
    if frame.empty:
        return {"strategies": [catalog[key] for key in requested]}

    higher_timeframe = interval in {"1d", "1w"}
    group_columns: str | list[str] = "contract" if higher_timeframe else [
        "contract", "session", "trading_date"
    ]
    groups = list(frame.groupby(group_columns, sort=False))
    for group_index, (_, session_bars) in enumerate(groups):
        session_bars = session_bars.reset_index(drop=True)
        force_final = force_close_last or group_index < len(groups) - 1
        if "orb" in requested and not higher_timeframe:
            values = resolved["orb"]
            entries, indicators = _orb_entries(session_bars, values)
            orb_visualization = visualization(
                "orb",
                str(catalog["orb"]["name"]),
                values,
                overlays=[
                    series(session_bars, indicators["upper"], key="opening_range_upper", label="Opening Range High", panel="price", color="#38bdf8"),
                    series(session_bars, indicators["lower"], key="opening_range_lower", label="Opening Range Low", panel="price", color="#38bdf8"),
                ],
                diagnostics=[
                    series(session_bars, indicators["volume_ratio"], key="volume_ratio", label="Volume Ratio", panel="strategy", color="#38bdf8"),
                    threshold(session_bars, float(values["volume_multiplier"]), key="volume_threshold", label="Required Ratio"),
                ],
            )
            _append_visualization(catalog["orb"], orb_visualization)
            catalog["orb"]["signals"].extend(
                simulate_signals(
                    session_bars,
                    "orb",
                    entries,
                    force_final=force_final,
                    risk=RiskConfig(
                        float(values["stop_loss_pct"]),
                        float(values["take_profit_pct"]),
                    ),
                    policy=_signal_simulation_policy("orb"),
                    diagnostic_context=diagnostic_context(
                        session_bars, indicators
                    ),
                    entry_reasons=_entry_reasons("orb", entries),
                )
            )
        if "bnf" in requested:
            values = resolved["bnf"]
            entries, exits, indicators = _bnf_signals(session_bars, values)
            bnf_visualization = visualization(
                "bnf",
                str(catalog["bnf"]["name"]),
                values,
                overlays=[series(session_bars, indicators["mean"], key="bnf_mean", label="Mean", panel="price", color="#a78bfa")],
                diagnostics=[
                    series(session_bars, indicators["z_score"], key="z_score", label="Z-score", panel="strategy", color="#a78bfa"),
                    threshold(session_bars, float(values["entry_z_score"]), key="entry_z_upper", label="Entry +"),
                    threshold(session_bars, -float(values["entry_z_score"]), key="entry_z_lower", label="Entry -"),
                    threshold(session_bars, float(values["exit_z_score"]), key="exit_z_upper", label="Exit +", color="#475569"),
                    threshold(session_bars, -float(values["exit_z_score"]), key="exit_z_lower", label="Exit -", color="#475569"),
                ],
            )
            _append_visualization(catalog["bnf"], bnf_visualization)
            catalog["bnf"]["signals"].extend(
                simulate_signals(
                    session_bars,
                    "bnf",
                    entries,
                    exits,
                    force_final=force_final,
                    risk=RiskConfig(
                        float(values["stop_loss_pct"]),
                        float(values["take_profit_pct"]),
                    ),
                    policy=_signal_simulation_policy("bnf"),
                    diagnostic_context=indicators[["mean", "z_score", "rsi"]],
                    entry_reasons=_entry_reasons("bnf", entries),
                )
            )
        for key in requested:
            if key in {"orb", "bnf"}:
                continue
            if higher_timeframe and key == "vwap_reversion":
                continue
            values = resolved[key]
            if key in _DOW_CHANNEL_ENTRY_MODES:
                entries, exits, channels = _dow_channel_analysis(
                    key, session_bars, values
                )
                overlay = serialize_channel_overlay(session_bars, channels)
                existing_overlays = catalog[key]["overlays"]
                if existing_overlays:
                    existing_overlays[0]["points"].extend(overlay["points"])
                else:
                    existing_overlays.append(overlay)
                strategy_visualization, context = _dow_visualization(
                    key,
                    str(catalog[key]["name"]),
                    session_bars,
                    values,
                    channels,
                    overlay,
                )
                _append_visualization(catalog[key], strategy_visualization)
            else:
                entries, exits, overlays, diagnostics, context = _technical_signals(
                    key, session_bars, values
                )
                _append_visualization(
                    catalog[key],
                    visualization(
                        key,
                        str(catalog[key]["name"]),
                        values,
                        overlays=overlays,
                        diagnostics=diagnostics,
                    ),
                )
            catalog[key]["signals"].extend(
                simulate_signals(
                    session_bars,
                    key,
                    entries,
                    exits,
                    force_final=force_final,
                    risk=RiskConfig(
                        float(values["stop_loss_pct"]),
                        float(values["take_profit_pct"]),
                    ),
                    policy=_signal_simulation_policy(key),
                    diagnostic_context=context,
                    entry_reasons=_entry_reasons(key, entries),
                )
            )
    return {"strategies": [catalog[key] for key in requested]}
