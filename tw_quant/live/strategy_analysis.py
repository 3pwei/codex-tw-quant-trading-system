from __future__ import annotations

from datetime import time
from typing import Iterable

import pandas as pd

from ..strategies import BNFMeanReversion, BNFMeanReversionConfig
from .models import KBar


SUPPORTED_STRATEGIES = ("orb", "bnf")


def _frame(bars: Iterable[KBar]) -> pd.DataFrame:
    rows = [
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
    return pd.DataFrame(rows)


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


def _simulate(
    bars: pd.DataFrame,
    strategy: str,
    entries: pd.Series,
    exits: pd.DataFrame | None = None,
    force_final: bool = False,
    stop_loss_pct: float = 0.006,
    take_profit_pct: float = 0.012,
) -> list[dict[str, object]]:
    signals: list[dict[str, object]] = []
    position = 0
    pending_entry = 0
    pending_exit = False
    entry_price = 0.0
    stop_price = 0.0
    target_price = 0.0

    def emit(event: str, row: pd.Series, price: float, reason: str) -> None:
        signals.append(
            {
                "strategy": strategy,
                "event": event,
                "direction": "long" if position == 1 else "short",
                "time": row["timestamp"].isoformat(timespec="milliseconds"),
                "price": round(float(price), 4),
                "stop_loss_price": round(stop_price, 4),
                "take_profit_price": round(target_price, 4),
                "reason": reason,
                "contract": row["contract"],
                "session": row["session"],
                "trading_date": row["trading_date"],
            }
        )

    for index, row in bars.iterrows():
        if position and pending_exit:
            emit("exit", row, float(row["open"]), "mean_reversion")
            position = 0
            pending_exit = False

        if position == 0 and pending_entry:
            position = pending_entry
            entry_price = float(row["open"])
            stop_price = entry_price * (
                1 - stop_loss_pct if position == 1 else 1 + stop_loss_pct
            )
            target_price = entry_price * (
                1 + take_profit_pct if position == 1 else 1 - take_profit_pct
            )
            emit("entry", row, entry_price, "signal_confirmed")
            pending_entry = 0

        if position:
            if position == 1 and float(row["low"]) <= stop_price:
                emit("exit", row, min(float(row["open"]), stop_price), "stop_loss")
                position = 0
            elif position == 1 and float(row["high"]) >= target_price:
                emit("exit", row, max(float(row["open"]), target_price), "take_profit")
                position = 0
            elif position == -1 and float(row["high"]) >= stop_price:
                emit("exit", row, max(float(row["open"]), stop_price), "stop_loss")
                position = 0
            elif position == -1 and float(row["low"]) <= target_price:
                emit("exit", row, min(float(row["open"]), target_price), "take_profit")
                position = 0

        if position and exits is not None:
            side = "long" if position == 1 else "short"
            pending_exit = bool(exits.loc[index, side])

        if position == 0 and not signals:
            candidate = int(entries.loc[index])
            if candidate in (-1, 1):
                pending_entry = candidate

    if force_final and position:
        row = bars.iloc[-1]
        emit("exit", row, float(row["close"]), "session_end")
    return signals


def analyze_live_strategies(
    bars: Iterable[KBar], selected: Iterable[str] = SUPPORTED_STRATEGIES
) -> dict[str, object]:
    requested = tuple(dict.fromkeys(value.lower() for value in selected))
    unsupported = sorted(set(requested) - set(SUPPORTED_STRATEGIES))
    if unsupported:
        raise ValueError(f"unsupported strategies: {', '.join(unsupported)}")

    frame = _frame(bars)
    catalog = {
        "orb": {
            "key": "orb",
            "name": "ORB 開盤區間突破",
            "color": "#38bdf8",
            "parameters": {
                "opening_range_minutes": 15,
                "volume_window": 5,
                "volume_multiplier": 1.2,
                "stop_loss_pct": 0.006,
                "take_profit_pct": 0.012,
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
                "stop_loss_pct": 0.006,
                "take_profit_pct": 0.012,
            },
            "signals": [],
        },
    }
    if frame.empty:
        return {"strategies": [catalog[key] for key in requested]}

    group_columns = ["contract", "session", "trading_date"]
    groups = list(frame.groupby(group_columns, sort=False))
    for group_index, (_, session_bars) in enumerate(groups):
        session_bars = session_bars.reset_index(drop=True)
        force_final = group_index < len(groups) - 1
        if "orb" in requested:
            catalog["orb"]["signals"].extend(
                _simulate(
                    session_bars,
                    "orb",
                    _orb_entries(session_bars),
                    force_final=force_final,
                )
            )
        if "bnf" in requested:
            entries, exits = _bnf_signals(session_bars)
            catalog["bnf"]["signals"].extend(
                _simulate(
                    session_bars,
                    "bnf",
                    entries,
                    exits,
                    force_final=force_final,
                )
            )
    return {"strategies": [catalog[key] for key in requested]}
