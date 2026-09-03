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


def analyze_strategies(
    bars: Iterable[KBar],
    selected: Iterable[str] = SUPPORTED_STRATEGIES,
    *,
    force_close_last: bool = False,
    parameters: dict[str, dict[str, object]] | None = None,
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

    groups = list(frame.groupby(["contract", "session", "trading_date"], sort=False))
    for group_index, (_, session_bars) in enumerate(groups):
        session_bars = session_bars.reset_index(drop=True)
        force_final = force_close_last or group_index < len(groups) - 1
        if "orb" in requested:
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
    return {"strategies": [catalog[key] for key in requested]}
