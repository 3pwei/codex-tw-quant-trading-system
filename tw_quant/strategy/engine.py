from __future__ import annotations

from datetime import time
from typing import Iterable

import pandas as pd

from ..execution import simulate_signals
from ..market import KBar
from ..risk import RiskConfig
from .definitions import BNFMeanReversion, BNFMeanReversionConfig
from .parameters import (
    SUPPORTED_STRATEGIES,
    strategy_catalog,
    validate_strategy_parameters,
)


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
) -> pd.Series:
    result = pd.Series(0, index=bars.index, dtype="int8")
    opening_range = int(parameters["opening_range_minutes"])
    if bars.empty:
        return result
    expected_open = time(8, 45) if bars.iloc[0]["session"] == "day" else time(15, 0)
    if bars.iloc[0]["timestamp"].time().replace(tzinfo=None) != expected_open:
        return result

    range_end = bars.iloc[0]["timestamp"] + pd.Timedelta(minutes=opening_range)
    opening = bars.loc[bars["timestamp"] < range_end]
    if len(opening) < 2 or not bool((bars["timestamp"] >= range_end).any()):
        return result
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
    return result


def _bnf_signals(
    bars: pd.DataFrame, parameters: dict[str, int | float]
) -> tuple[pd.Series, pd.DataFrame]:
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
    entries = strategy.generate_entries(bars)
    exits = strategy.generate_exits(bars)
    forming = bars["status"] != "closed"
    entries.loc[forming] = 0
    exits.loc[forming, ["long", "short"]] = False
    return entries, exits


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


def _technical_signals(
    key: str, bars: pd.DataFrame, parameters: dict[str, int | float]
) -> tuple[pd.Series, pd.DataFrame | None]:
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    volume = bars["volume"].astype(float)

    if key == "ma_crossover":
        fast = close.rolling(int(parameters["short_window"]), min_periods=int(parameters["short_window"])).mean()
        slow = close.rolling(int(parameters["long_window"]), min_periods=int(parameters["long_window"])).mean()
        entries = _cross_entries(fast, slow)
        exits = _opposite_exits(entries)
    elif key == "ema_trend":
        fast = close.ewm(span=int(parameters["fast_period"]), adjust=False, min_periods=int(parameters["fast_period"])).mean()
        slow = close.ewm(span=int(parameters["slow_period"]), adjust=False, min_periods=int(parameters["slow_period"])).mean()
        entries = _cross_entries(fast, slow)
        exits = _opposite_exits(entries)
    elif key == "donchian_breakout":
        window = int(parameters["lookback_period"])
        upper = high.rolling(window, min_periods=window).max().shift(1)
        lower = low.rolling(window, min_periods=window).min().shift(1)
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[close > upper] = 1
        entries.loc[close < lower] = -1
        midpoint = (upper + lower) / 2
        exits = pd.DataFrame({"long": close < midpoint, "short": close > midpoint}, index=bars.index)
    elif key == "rsi_mean_reversion":
        indicator = _rsi(close, int(parameters["rsi_period"]))
        entries = pd.Series(0, index=bars.index, dtype="int8")
        entries.loc[(indicator <= float(parameters["oversold_rsi"])) & (indicator.shift(1) > float(parameters["oversold_rsi"]))] = 1
        entries.loc[(indicator >= float(parameters["overbought_rsi"])) & (indicator.shift(1) < float(parameters["overbought_rsi"]))] = -1
        exit_level = float(parameters["exit_rsi"])
        exits = pd.DataFrame({"long": indicator >= exit_level, "short": indicator <= exit_level}, index=bars.index)
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
    elif key == "macd_momentum":
        fast = close.ewm(span=int(parameters["fast_period"]), adjust=False, min_periods=int(parameters["fast_period"])).mean()
        slow = close.ewm(span=int(parameters["slow_period"]), adjust=False, min_periods=int(parameters["slow_period"])).mean()
        macd = fast - slow
        signal = macd.ewm(span=int(parameters["signal_period"]), adjust=False, min_periods=int(parameters["signal_period"])).mean()
        entries = _cross_entries(macd, signal)
        exits = _opposite_exits(entries)
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
    else:  # pragma: no cover - guarded by SUPPORTED_STRATEGIES
        raise ValueError(f"unsupported strategy: {key}")

    forming = bars["status"] != "closed"
    entries.loc[forming] = 0
    if exits is not None:
        exits.loc[forming, ["long", "short"]] = False
        exits = exits.fillna(False)
    return entries, exits


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
            catalog["orb"]["signals"].extend(
                simulate_signals(
                    session_bars,
                    "orb",
                    _orb_entries(session_bars, values),
                    force_final=force_final,
                    risk=RiskConfig(
                        float(values["stop_loss_pct"]),
                        float(values["take_profit_pct"]),
                    ),
                )
            )
        if "bnf" in requested:
            values = resolved["bnf"]
            entries, exits = _bnf_signals(session_bars, values)
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
                )
            )
        for key in requested:
            if key in {"orb", "bnf"}:
                continue
            if higher_timeframe and key == "vwap_reversion":
                continue
            values = resolved[key]
            entries, exits = _technical_signals(key, session_bars, values)
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
                )
            )
    return {"strategies": [catalog[key] for key in requested]}
