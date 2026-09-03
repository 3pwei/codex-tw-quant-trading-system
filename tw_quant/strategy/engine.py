from __future__ import annotations

from datetime import time
from typing import Iterable

import pandas as pd

from ..execution import simulate_signals
from ..market import KBar
from ..risk import DEFAULT_RISK
from .definitions import BNFMeanReversion, BNFMeanReversionConfig


SUPPORTED_STRATEGIES = ("orb", "bnf")


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


def _orb_entries(bars: pd.DataFrame) -> pd.Series:
    result = pd.Series(0, index=bars.index, dtype="int8")
    if len(bars) < 16:
        return result
    expected_open = time(8, 45) if bars.iloc[0]["session"] == "day" else time(15, 0)
    if bars.iloc[0]["timestamp"].time().replace(tzinfo=None) != expected_open:
        return result

    opening = bars.iloc[:15]
    range_high = float(opening["high"].max())
    range_low = float(opening["low"].min())
    previous_close = bars["close"].shift(1)
    baseline_volume = bars["volume"].rolling(5, min_periods=1).mean().shift(1)
    closed = bars["status"] == "closed"
    eligible = (
        (bars.index >= 15)
        & closed
        & (bars["volume"] >= baseline_volume * 1.2)
    )
    long_break = eligible & (bars["close"] > range_high) & (previous_close <= range_high)
    short_break = eligible & (bars["close"] < range_low) & (previous_close >= range_low)
    candidates = [(int(index), 1) for index in bars.index[long_break]]
    candidates += [(int(index), -1) for index in bars.index[short_break]]
    if candidates:
        index, direction = min(candidates, key=lambda item: item[0])
        result.loc[index] = direction
    return result


def _bnf_signals(bars: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    strategy = BNFMeanReversion(BNFMeanReversionConfig(direction="both"))
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
) -> dict[str, object]:
    """Analyze canonical bars independent of whether they came from live or history."""
    requested = tuple(dict.fromkeys(value.lower() for value in selected))
    unsupported = sorted(set(requested) - set(SUPPORTED_STRATEGIES))
    if unsupported:
        raise ValueError(f"unsupported strategies: {', '.join(unsupported)}")

    frame = _frame(bars)
    risk_parameters = {
        "stop_loss_pct": DEFAULT_RISK.stop_loss_pct,
        "take_profit_pct": DEFAULT_RISK.take_profit_pct,
    }
    catalog: dict[str, dict[str, object]] = {
        "orb": {
            "key": "orb",
            "name": "ORB 開盤區間突破",
            "color": "#38bdf8",
            "parameters": {
                "opening_range_minutes": 15,
                "volume_window": 5,
                "volume_multiplier": 1.2,
                **risk_parameters,
            },
            "signals": [],
        },
        "bnf": {
            "key": "bnf",
            "name": "BNF 均值回歸",
            "color": "#a78bfa",
            "parameters": {
                "mean_window": 20,
                "std_window": 20,
                "entry_z_score": 2.0,
                "exit_z_score": 0.5,
                "rsi_period": 14,
                "oversold_rsi": 30,
                "overbought_rsi": 70,
                **risk_parameters,
            },
            "signals": [],
        },
    }
    if frame.empty:
        return {"strategies": [catalog[key] for key in requested]}

    groups = list(frame.groupby(["contract", "session", "trading_date"], sort=False))
    for group_index, (_, session_bars) in enumerate(groups):
        session_bars = session_bars.reset_index(drop=True)
        force_final = force_close_last or group_index < len(groups) - 1
        if "orb" in requested:
            catalog["orb"]["signals"].extend(
                simulate_signals(
                    session_bars,
                    "orb",
                    _orb_entries(session_bars),
                    force_final=force_final,
                    risk=DEFAULT_RISK,
                )
            )
        if "bnf" in requested:
            entries, exits = _bnf_signals(session_bars)
            catalog["bnf"]["signals"].extend(
                simulate_signals(
                    session_bars,
                    "bnf",
                    entries,
                    exits,
                    force_final=force_final,
                    risk=DEFAULT_RISK,
                )
            )
    return {"strategies": [catalog[key] for key in requested]}
