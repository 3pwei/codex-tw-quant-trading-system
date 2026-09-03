from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuturesCostConfig:
    """Execution-cost assumptions shared by TMF backtest entry points."""

    multiplier: float = 10.0
    commission_per_side: float = 10.0
    tax_rate: float = 0.00002
    slippage_points: float = 1.0

    def side_cost(self, price: float, contracts: int = 1) -> tuple[float, float]:
        commission = self.commission_per_side * contracts
        tax = round(price * self.multiplier * contracts * self.tax_rate)
        return commission, tax
