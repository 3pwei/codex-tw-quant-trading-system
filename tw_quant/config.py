from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class CostConfig:
    """台股現股當沖成本；券商折扣與最低手續費應依實際帳戶調整。"""

    commission_rate: float = 0.001425
    commission_discount: float = 1.0
    min_commission: float = 20.0
    sell_tax_rate: float = 0.0015
    slippage_bps: float = 2.0
    round_cost_to_ntd: bool = True

    def __post_init__(self) -> None:
        for name in (
            "commission_rate",
            "commission_discount",
            "min_commission",
            "sell_tax_rate",
            "slippage_bps",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 不可為負數")


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital: float = 1_000_000.0
    quantity: int = 1_000
    stop_loss_pct: float = 0.006
    take_profit_pct: float = 0.012
    force_exit_time: time = time(13, 20)
    max_trades_per_day: int = 1
    allow_short: bool = False

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital 必須大於 0")
        if self.quantity <= 0:
            raise ValueError("quantity 必須大於 0")
        if not 0 < self.stop_loss_pct < 1:
            raise ValueError("stop_loss_pct 必須介於 0 與 1 之間")
        if not 0 < self.take_profit_pct < 1:
            raise ValueError("take_profit_pct 必須介於 0 與 1 之間")
        if self.max_trades_per_day <= 0:
            raise ValueError("max_trades_per_day 必須大於 0")

