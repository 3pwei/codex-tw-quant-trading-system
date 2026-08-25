from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from .config import CostConfig


Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class OrderCost:
    notional: float
    commission: float
    tax: float

    @property
    def total(self) -> float:
        return self.commission + self.tax


class TaiwanStockCostModel:
    def __init__(self, config: CostConfig):
        self.config = config

    def fill_price(self, raw_price: float, side: Side) -> float:
        fraction = self.config.slippage_bps / 10_000
        multiplier = 1 + fraction if side == "buy" else 1 - fraction
        return raw_price * multiplier

    def order_cost(self, price: float, quantity: int, side: Side) -> OrderCost:
        notional = price * quantity
        commission = notional * self.config.commission_rate * self.config.commission_discount
        if commission > 0:
            commission = max(self.config.min_commission, commission)
        tax = notional * self.config.sell_tax_rate if side == "sell" else 0.0

        if self.config.round_cost_to_ntd:
            commission = math.floor(commission + 1e-12)
            tax = math.floor(tax + 1e-12)

        return OrderCost(notional=notional, commission=commission, tax=tax)

