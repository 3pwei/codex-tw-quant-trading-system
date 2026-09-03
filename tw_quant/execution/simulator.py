from __future__ import annotations

import pandas as pd

from ..risk import DEFAULT_RISK, RiskConfig, RiskLevels, calculate_levels, triggered_exit


def simulate_signals(
    bars: pd.DataFrame,
    strategy: str,
    entries: pd.Series,
    exits: pd.DataFrame | None = None,
    *,
    force_final: bool = False,
    risk: RiskConfig = DEFAULT_RISK,
) -> list[dict[str, object]]:
    """Apply next-open fills and shared risk rules to strategy intents."""
    signals: list[dict[str, object]] = []
    position = 0
    pending_entry = 0
    pending_exit = False
    levels = RiskLevels(0.0, 0.0)

    def emit(event: str, row: pd.Series, price: float, reason: str) -> None:
        signals.append(
            {
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
        )

    for index, row in bars.iterrows():
        if position and pending_exit:
            emit("exit", row, float(row["open"]), "mean_reversion")
            position = 0
            pending_exit = False

        if position == 0 and pending_entry:
            position = pending_entry
            entry_price = float(row["open"])
            levels = calculate_levels(entry_price, position, risk)
            emit("entry", row, entry_price, "signal_confirmed")
            pending_entry = 0

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
                emit("exit", row, price, reason)
                position = 0

        if position and exits is not None:
            side = "long" if position == 1 else "short"
            pending_exit = bool(exits.loc[index, side])

        # Current ORB/BNF policy permits one completed signal sequence per session.
        if position == 0 and not signals:
            candidate = int(entries.loc[index])
            if candidate in (-1, 1):
                pending_entry = candidate

    if force_final and position:
        row = bars.iloc[-1]
        emit("exit", row, float(row["close"]), "session_end")
    return signals
