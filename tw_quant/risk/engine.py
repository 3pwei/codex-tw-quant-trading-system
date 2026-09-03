from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Direction = Literal[1, -1]


@dataclass(frozen=True)
class RiskConfig:
    stop_loss_pct: float = 0.006
    take_profit_pct: float = 0.012

    def __post_init__(self) -> None:
        if not 0 < self.stop_loss_pct < 1:
            raise ValueError("stop_loss_pct must be between 0 and 1")
        if not 0 < self.take_profit_pct < 1:
            raise ValueError("take_profit_pct must be between 0 and 1")


@dataclass(frozen=True)
class RiskLevels:
    stop_loss_price: float
    take_profit_price: float


DEFAULT_RISK = RiskConfig()


def calculate_levels(
    entry_price: float,
    direction: Direction,
    config: RiskConfig = DEFAULT_RISK,
) -> RiskLevels:
    if entry_price <= 0:
        raise ValueError("entry_price must be positive")
    if direction not in (1, -1):
        raise ValueError("direction must be 1 or -1")
    return RiskLevels(
        stop_loss_price=entry_price * (
            1 - config.stop_loss_pct if direction == 1 else 1 + config.stop_loss_pct
        ),
        take_profit_price=entry_price * (
            1 + config.take_profit_pct if direction == 1 else 1 - config.take_profit_pct
        ),
    )


def triggered_exit(
    *,
    direction: Direction,
    open_price: float,
    high: float,
    low: float,
    levels: RiskLevels,
) -> tuple[float, str] | None:
    """Return a conservative risk exit; stop loss wins same-bar ambiguity."""
    if direction == 1:
        if low <= levels.stop_loss_price:
            return min(open_price, levels.stop_loss_price), "stop_loss"
        if high >= levels.take_profit_price:
            return max(open_price, levels.take_profit_price), "take_profit"
    else:
        if high >= levels.stop_loss_price:
            return max(open_price, levels.stop_loss_price), "stop_loss"
        if low <= levels.take_profit_price:
            return min(open_price, levels.take_profit_price), "take_profit"
    return None
