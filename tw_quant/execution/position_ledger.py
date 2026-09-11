from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from ..events import (
    BarClosedEvent,
    DomainEvent,
    EventMetadata,
    FillEvent,
    PositionEvent,
)


@dataclass(frozen=True)
class PositionKey:
    owner_id: str
    strategy_id: str
    strategy_version: int
    symbol: str
    contract: str


@dataclass
class PositionState:
    key: PositionKey
    quantity: int = 0
    average_price: float = 0.0
    opened_at: datetime | None = None
    entry_commission: float = 0.0
    entry_tax: float = 0.0
    realized_pnl: float = 0.0
    total_cost: float = 0.0
    order_source: Literal["manual", "strategy_auto"] = "manual"
    runtime_id: str | None = None
    decision_id: str | None = None


@dataclass(frozen=True)
class RealizedTrade:
    owner_id: str
    strategy_id: str
    strategy_version: int
    symbol: str
    contract: str
    direction: Literal["long", "short"]
    quantity: int
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    gross_pnl: float
    commission: float
    tax: float
    total_cost: float
    net_pnl: float
    exit_reason: str
    exit_order_id: str
    trading_date: date | None


class PositionLedger:
    """Owner-scoped futures positions derived only from immutable fills."""

    def __init__(self, *, multiplier: float = 10.0):
        if multiplier <= 0:
            raise ValueError("multiplier must be positive")
        self.multiplier = multiplier
        self._positions: dict[PositionKey, PositionState] = {}
        self._seen_fills: set[str] = set()
        self.trades: list[RealizedTrade] = []

    @staticmethod
    def _owner(event: DomainEvent) -> str:
        owner_id = event.meta.owner_id
        if not owner_id:
            raise ValueError(f"{event.kind} execution event requires owner_id")
        return owner_id

    def state(
        self,
        owner_id: str,
        strategy_id: str,
        strategy_version: int,
        symbol: str,
        contract: str,
    ) -> PositionState | None:
        return self._positions.get(
            PositionKey(owner_id, strategy_id, strategy_version, symbol, contract)
        )

    def open_positions(self) -> tuple[PositionState, ...]:
        return tuple(state for state in self._positions.values() if state.quantity != 0)

    @staticmethod
    def _fill_cost(fill: FillEvent) -> float:
        return fill.commission + fill.tax

    def _unrealized(self, state: PositionState, mark_price: float) -> float:
        if state.quantity == 0:
            return 0.0
        direction = 1 if state.quantity > 0 else -1
        gross = (
            (mark_price - state.average_price)
            * direction
            * abs(state.quantity)
            * self.multiplier
        )
        return gross - state.entry_commission - state.entry_tax

    def _event(
        self,
        state: PositionState,
        *,
        cause: DomainEvent,
        mark_price: float,
    ) -> PositionEvent:
        source_key = ":".join(
            (
                cause.meta.event_id,
                state.key.owner_id,
                state.key.strategy_id,
                str(state.key.strategy_version),
                state.key.contract,
            )
        )
        return PositionEvent(
            meta=EventMetadata.create(
                kind="position",
                occurred_at=cause.meta.occurred_at,
                source="position_ledger",
                source_key=source_key,
                causation_id=cause.meta.event_id,
                correlation_id=cause.meta.correlation_id,
                owner_id=state.key.owner_id,
            ),
            strategy_id=state.key.strategy_id,
            strategy_version=state.key.strategy_version,
            symbol=state.key.symbol,
            contract=state.key.contract,
            quantity=state.quantity,
            average_price=state.average_price,
            realized_pnl=state.realized_pnl,
            unrealized_pnl=self._unrealized(state, mark_price),
            total_cost=state.total_cost,
            order_source=state.order_source,
            runtime_id=state.runtime_id,
            decision_id=state.decision_id,
        )

    def on_fill(self, event: DomainEvent) -> list[PositionEvent] | None:
        if not isinstance(event, FillEvent):
            raise TypeError("PositionLedger.on_fill requires FillEvent")
        if event.fill_id in self._seen_fills:
            return None
        self._seen_fills.add(event.fill_id)
        owner_id = self._owner(event)
        key = PositionKey(
            owner_id,
            event.strategy_id,
            event.strategy_version,
            event.symbol,
            event.contract,
        )
        state = self._positions.setdefault(key, PositionState(key))
        if state.quantity == 0:
            state.order_source = event.order_source
            state.runtime_id = event.runtime_id
            state.decision_id = event.decision_id
        signed_fill = event.quantity if event.side == "buy" else -event.quantity
        old_quantity = state.quantity
        old_abs = abs(old_quantity)
        fill_abs = abs(signed_fill)
        fill_cost = self._fill_cost(event)
        state.total_cost += fill_cost

        if old_quantity == 0 or old_quantity * signed_fill > 0:
            combined = old_abs + fill_abs
            state.average_price = (
                state.average_price * old_abs + event.price * fill_abs
            ) / combined
            state.quantity += signed_fill
            state.entry_commission += event.commission
            state.entry_tax += event.tax
            if old_quantity == 0:
                state.opened_at = event.meta.occurred_at
            return [self._event(state, cause=event, mark_price=event.price)]

        closing_quantity = min(old_abs, fill_abs)
        opening_ratio = closing_quantity / old_abs
        fill_close_ratio = closing_quantity / fill_abs
        allocated_entry_commission = state.entry_commission * opening_ratio
        allocated_entry_tax = state.entry_tax * opening_ratio
        closing_commission = event.commission * fill_close_ratio
        closing_tax = event.tax * fill_close_ratio
        direction = 1 if old_quantity > 0 else -1
        gross_pnl = (
            (event.price - state.average_price)
            * direction
            * closing_quantity
            * self.multiplier
        )
        commission = allocated_entry_commission + closing_commission
        tax = allocated_entry_tax + closing_tax
        net_pnl = gross_pnl - commission - tax
        entry_time = state.opened_at or event.meta.occurred_at
        self.trades.append(
            RealizedTrade(
                owner_id=owner_id,
                strategy_id=event.strategy_id,
                strategy_version=event.strategy_version,
                symbol=event.symbol,
                contract=event.contract,
                direction="long" if old_quantity > 0 else "short",
                quantity=closing_quantity,
                entry_time=entry_time,
                exit_time=event.meta.occurred_at,
                entry_price=state.average_price,
                exit_price=event.price,
                gross_pnl=gross_pnl,
                commission=commission,
                tax=tax,
                total_cost=commission + tax,
                net_pnl=net_pnl,
                exit_reason=event.reason,
                exit_order_id=event.order_id,
                trading_date=event.trading_date,
            )
        )
        state.realized_pnl += net_pnl
        state.entry_commission -= allocated_entry_commission
        state.entry_tax -= allocated_entry_tax
        state.quantity += signed_fill

        if state.quantity == 0:
            state.average_price = 0.0
            state.opened_at = None
            state.entry_commission = 0.0
            state.entry_tax = 0.0
        elif old_quantity * state.quantity < 0:
            remaining_ratio = (fill_abs - closing_quantity) / fill_abs
            state.average_price = event.price
            state.opened_at = event.meta.occurred_at
            state.entry_commission = event.commission * remaining_ratio
            state.entry_tax = event.tax * remaining_ratio

        return [self._event(state, cause=event, mark_price=event.price)]

    def mark_to_market(self, event: DomainEvent) -> list[PositionEvent] | None:
        if not isinstance(event, BarClosedEvent):
            raise TypeError("PositionLedger.mark_to_market requires BarClosedEvent")
        events = [
            self._event(state, cause=event, mark_price=event.close)
            for state in self.open_positions()
            if state.key.symbol == event.symbol and state.key.contract == event.contract
        ]
        return events or None
