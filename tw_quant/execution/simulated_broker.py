from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..events import (
    BarClosedEvent,
    DomainEvent,
    EventMetadata,
    FillEvent,
    OrderIntent,
    RiskDecision,
    SessionEvent,
)
from ..futures_costs import FuturesCostConfig
from .position_ledger import PositionKey, PositionLedger


OrderStatus = Literal["pending_risk", "approved", "rejected", "filled"]


@dataclass
class OrderRecord:
    intent: OrderIntent
    status: OrderStatus = "pending_risk"
    approved_quantity: int = 0
    status_reason: str = "awaiting_risk"
    fill_id: str | None = None


class SimulatedBroker:
    """Deterministic market-order simulator; never calls an external broker."""

    def __init__(
        self,
        costs: FuturesCostConfig | None = None,
        position_ledger: PositionLedger | None = None,
        *,
        allow_signal_price_execution: bool = False,
    ):
        self.costs = costs or FuturesCostConfig()
        self.position_ledger = position_ledger
        self.allow_signal_price_execution = allow_signal_price_execution
        self.orders: dict[str, OrderRecord] = {}
        self._order_sequence: list[str] = []
        self._last_close: dict[tuple[str, str], float] = {}
        self._bars_with_fills: set[str] = set()

    def update_market_price(self, symbol: str, contract: str, price: float) -> None:
        """Seed the latest server-side market price for an immediate paper fill."""
        if not symbol or not contract:
            raise ValueError("symbol and contract are required")
        if price <= 0:
            raise ValueError("market price must be positive")
        self._last_close[(symbol, contract)] = price

    def on_order(self, event: DomainEvent) -> None:
        if not isinstance(event, OrderIntent):
            raise TypeError("SimulatedBroker.on_order requires OrderIntent")
        PositionLedger._owner(event)
        existing = self.orders.get(event.order_id)
        if existing is not None:
            return None
        self.orders[event.order_id] = OrderRecord(event)
        self._order_sequence.append(event.order_id)
        return None

    def _fill(
        self,
        record: OrderRecord,
        *,
        raw_price: float,
        occurred_at: datetime,
        cause: DomainEvent,
        quantity: int | None = None,
    ) -> FillEvent:
        order = record.intent
        fill_quantity = quantity or record.approved_quantity
        direction = 1 if order.side == "buy" else -1
        price = raw_price + self.costs.slippage_points * direction
        commission, tax = self.costs.side_cost(price, fill_quantity)
        fill_meta = EventMetadata.create(
            kind="fill",
            occurred_at=occurred_at,
            source="simulated_broker",
            source_key=f"{order.order_id}:full",
            causation_id=cause.meta.event_id,
            correlation_id=order.meta.correlation_id,
            owner_id=order.meta.owner_id,
        )
        fill = FillEvent(
            meta=fill_meta,
            fill_id=fill_meta.event_id,
            order_id=order.order_id,
            strategy_id=order.strategy_id,
            strategy_version=order.strategy_version,
            symbol=order.symbol,
            contract=order.contract,
            side=order.side,
            quantity=fill_quantity,
            price=price,
            commission=commission,
            tax=tax,
            slippage=self.costs.slippage_points,
            purpose=order.purpose,
            reason=order.reason,
            trading_date=order.trading_date,
            order_source=order.order_source,
            runtime_id=order.runtime_id,
            decision_id=order.decision_id,
        )
        record.status = "filled"
        record.status_reason = "simulated_fill"
        record.fill_id = fill.fill_id
        return fill

    def _reduce_only_available(self, record: OrderRecord) -> int:
        order = record.intent
        if not order.reduce_only:
            return record.approved_quantity
        if self.position_ledger is None:
            raise RuntimeError("reduce_only execution requires a position ledger")
        owner_id = PositionLedger._owner(order)
        state = self.position_ledger.state(
            owner_id,
            order.strategy_id,
            order.strategy_version,
            order.symbol,
            order.contract,
        )
        if state is None or state.quantity == 0:
            return 0
        closes_long = state.quantity > 0 and order.side == "sell"
        closes_short = state.quantity < 0 and order.side == "buy"
        if not (closes_long or closes_short):
            return 0
        return min(abs(state.quantity), record.approved_quantity)

    @staticmethod
    def _reject_unfillable_reduce_only(record: OrderRecord) -> None:
        record.status = "rejected"
        record.status_reason = "reduce_only_position_unavailable"

    def on_risk_decision(self, event: DomainEvent) -> list[FillEvent] | None:
        if not isinstance(event, RiskDecision):
            raise TypeError("SimulatedBroker.on_risk_decision requires RiskDecision")
        record = self.orders.get(event.order_id)
        if record is None:
            raise ValueError(f"risk decision references unknown order: {event.order_id}")
        if record.status != "pending_risk":
            return None
        if not event.approved:
            record.status = "rejected"
            record.status_reason = event.reason
            return None
        if event.approved_quantity > record.intent.quantity:
            raise ValueError("risk decision cannot increase the requested quantity")
        record.status = "approved"
        record.approved_quantity = event.approved_quantity
        record.status_reason = event.reason
        if record.intent.execution_timing == "next_bar_open":
            return None
        if record.intent.execution_timing == "signal_price":
            if not self.allow_signal_price_execution:
                raise RuntimeError("signal_price execution is disabled")
            price = record.intent.reference_price
            if price <= 0:
                raise RuntimeError("signal_price order requires a positive reference price")
        else:
            price = self._last_close.get((record.intent.symbol, record.intent.contract))
            if price is None:
                raise RuntimeError("current_close order has no known closing price")
        quantity = self._reduce_only_available(record)
        if quantity == 0:
            self._reject_unfillable_reduce_only(record)
            return None
        return [
            self._fill(
                record,
                raw_price=price,
                occurred_at=event.meta.occurred_at,
                cause=event,
                quantity=quantity,
            )
        ]

    def on_bar(self, event: DomainEvent) -> list[FillEvent] | None:
        if not isinstance(event, BarClosedEvent):
            raise TypeError("SimulatedBroker.on_bar requires BarClosedEvent")
        self._last_close[(event.symbol, event.contract)] = event.close
        fills: list[FillEvent] = []
        reserved: dict[PositionKey, int] = {}
        for order_id in self._order_sequence:
            record = self.orders[order_id]
            order = record.intent
            if (
                record.status == "approved"
                and order.execution_timing == "next_bar_open"
                and order.symbol == event.symbol
                and order.contract == event.contract
                and order.meta.occurred_at < event.meta.occurred_at
            ):
                quantity = record.approved_quantity
                if order.reduce_only:
                    owner_id = PositionLedger._owner(order)
                    key = PositionKey(
                        owner_id,
                        order.strategy_id,
                        order.strategy_version,
                        order.symbol,
                        order.contract,
                    )
                    quantity = max(
                        0,
                        self._reduce_only_available(record) - reserved.get(key, 0),
                    )
                    if quantity == 0:
                        self._reject_unfillable_reduce_only(record)
                        continue
                    reserved[key] = reserved.get(key, 0) + quantity
                fills.append(
                    self._fill(
                        record,
                        raw_price=event.open,
                        occurred_at=event.meta.occurred_at,
                        cause=event,
                        quantity=quantity,
                    )
                )
        if fills:
            self._bars_with_fills.add(event.meta.event_id)
        return fills or None

    def on_session(self, event: DomainEvent) -> None:
        if not isinstance(event, SessionEvent):
            raise TypeError("SimulatedBroker.on_session requires SessionEvent")
        if event.action not in {"closing", "closed"}:
            return None
        for record in self.orders.values():
            order = record.intent
            if (
                record.status == "approved"
                and order.execution_timing == "next_bar_open"
                and order.symbol == event.symbol
                and order.contract == event.contract
            ):
                record.status = "rejected"
                record.status_reason = "session_closed_before_fill"
        return None

    def bar_emitted_fill(self, event_id: str) -> bool:
        return event_id in self._bars_with_fills
