from __future__ import annotations

from typing import Mapping

import pandas as pd

from ..risk import DEFAULT_RISK, RiskConfig, RiskLevels, calculate_levels, triggered_exit
from .policy import DEFAULT_SIGNAL_SIMULATION_POLICY, SignalSimulationPolicy


def simulate_signals(
    bars: pd.DataFrame,
    strategy: str,
    entries: pd.Series,
    exits: pd.DataFrame | None = None,
    *,
    force_final: bool = False,
    risk: RiskConfig = DEFAULT_RISK,
    policy: SignalSimulationPolicy = DEFAULT_SIGNAL_SIMULATION_POLICY,
    diagnostic_context: pd.DataFrame | None = None,
    entry_reasons: Mapping[int, str] | None = None,
) -> list[dict[str, object]]:
    """Apply next-open fills and shared risk rules to strategy intents."""
    signals: list[dict[str, object]] = []
    position = 0
    pending_entry = 0
    pending_exit = False
    previous_entry_intent = 0
    pending_entry_reason = "signal_confirmed"
    pending_entry_context: dict[str, object] = {}
    pending_exit_context: dict[str, object] = {}
    levels = RiskLevels(0.0, 0.0)

    def row_context(index: object) -> dict[str, object]:
        if diagnostic_context is None or index not in diagnostic_context.index:
            return {}
        result: dict[str, object] = {}
        for key, value in diagnostic_context.loc[index].items():
            if pd.notna(value):
                result[str(key)] = round(float(value), 8)
        return result

    def emit(
        event: str,
        row: pd.Series,
        price: float,
        reason: str,
        context: Mapping[str, object] | None = None,
        trigger_reason: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
                "strategy": strategy,
                "event": event,
                "direction": "long" if position == 1 else "short",
                "time": row["timestamp"].isoformat(timespec="milliseconds"),
                "price": round(float(price), 4),
                "stop_loss_price": round(levels.stop_loss_price, 4),
                "take_profit_price": round(levels.take_profit_price, 4),
                "reason": reason,
                "contract": row["contract"],
                "session": row["session"],
                "trading_date": row["trading_date"],
            }
        if context:
            payload["context"] = dict(context)
        if trigger_reason:
            payload["trigger_reason"] = trigger_reason
        signals.append(payload)

    for index, row in bars.iterrows():
        if position and pending_exit:
            emit(
                "exit",
                row,
                float(row["open"]),
                policy.strategy_exit_reason,
                pending_exit_context,
            )
            position = 0
            pending_exit = False

        if position == 0 and pending_entry:
            position = pending_entry
            entry_price = float(row["open"])
            levels = calculate_levels(entry_price, position, risk)
            emit(
                "entry", row, entry_price, "signal_confirmed",
                pending_entry_context, pending_entry_reason,
            )
            pending_entry = 0
            pending_entry_context = {}

        if position:
            risk_exit = triggered_exit(
                direction=position,
                open_price=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                levels=levels,
            )
            if risk_exit:
                price, reason = risk_exit
                emit("exit", row, price, reason, row_context(index))
                position = 0

        if position and exits is not None:
            side = "long" if position == 1 else "short"
            pending_exit = bool(exits.loc[index, side])
            if pending_exit:
                pending_exit_context = row_context(index)

        candidate = int(entries.loc[index])
        is_new_intent = candidate in (-1, 1) and candidate != previous_entry_intent
        previous_entry_intent = candidate
        if position == 0 and pending_entry == 0 and is_new_intent:
            pending_entry = candidate
            pending_entry_reason = (entry_reasons or {}).get(
                int(index), "signal_confirmed"
            )
            pending_entry_context = row_context(index)

    if force_final and position:
        row = bars.iloc[-1]
        emit("exit", row, float(row["close"]), "session_end", row_context(row.name))
    return signals
