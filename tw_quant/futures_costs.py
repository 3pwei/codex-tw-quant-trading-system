from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FuturesCostConfig:
    """Execution-cost assumptions shared by TMF backtest entry points."""

    multiplier: float = 10.0
    commission_per_side: float = 10.0
    tax_rate: float = 0.00002
    slippage_points: float = 1.0

    def __post_init__(self) -> None:
        if self.multiplier <= 0:
            raise ValueError("multiplier must be positive")
        for name in ("commission_per_side", "tax_rate", "slippage_points"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")

    def side_cost(self, price: float, contracts: int = 1) -> tuple[float, float]:
        if price <= 0 or contracts <= 0:
            raise ValueError("price and contracts must be positive")
        commission = self.commission_per_side * contracts
        tax = round(price * self.multiplier * contracts * self.tax_rate)
        return commission, tax
