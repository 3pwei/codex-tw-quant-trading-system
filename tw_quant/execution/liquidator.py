from __future__ import annotations

from collections.abc import Callable

from ..events import (
    BarClosedEvent,
    DomainEvent,
    EventMetadata,
    FillEvent,
    OrderIntent,
    SessionEvent,
)
from .position_ledger import PositionKey, PositionLedger, PositionState


class PositionLiquidator:
    """Create reduce-only liquidation intents for session end and contract roll."""

    def __init__(
        self,
        ledger: PositionLedger,
        has_pending_exit: Callable[[PositionKey], bool] | None = None,
    ):
        self.ledger = ledger
        self.has_pending_exit = has_pending_exit or (lambda _key: False)
        self._pending: set[PositionKey] = set()

    @staticmethod
    def _build_intent(
        state: PositionState,
        cause: DomainEvent,
        reason: str,
    ) -> OrderIntent:
        meta = EventMetadata.create(
            kind="order_intent",
            occurred_at=cause.meta.occurred_at,
            source="position_liquidator",
            source_key=(
                f"{cause.meta.event_id}:{state.key.owner_id}:"
                f"{state.key.strategy_id}:{state.key.contract}"
            ),
            causation_id=cause.meta.event_id,
            correlation_id=cause.meta.correlation_id,
            owner_id=state.key.owner_id,
        )
        return OrderIntent(
            meta=meta,
            order_id=meta.event_id,
            strategy_id=state.key.strategy_id,
            strategy_version=state.key.strategy_version,
            symbol=state.key.symbol,
            contract=state.key.contract,
            side="sell" if state.quantity > 0 else "buy",
            quantity=abs(state.quantity),
            purpose="liquidation",
            execution_timing="current_close",
            reduce_only=True,
            reason=reason,
            trading_date=(
                cause.trading_date
                if isinstance(cause, (BarClosedEvent, SessionEvent))
                else None
            ),
        )

    def _intents(
        self,
        states: list[PositionState],
        cause: DomainEvent,
        reason: str,
    ) -> list[OrderIntent] | None:
        orders: list[OrderIntent] = []
        for state in states:
            if state.key in self._pending or self.has_pending_exit(state.key):
                continue
            self._pending.add(state.key)
            orders.append(self._build_intent(state, cause, reason))
        return orders or None

    def on_session(self, event: DomainEvent) -> list[OrderIntent] | None:
        if not isinstance(event, SessionEvent):
            raise TypeError("PositionLiquidator.on_session requires SessionEvent")
        if event.action != "closing":
            return None
        states = [
            state
            for state in self.ledger.open_positions()
            if state.key.symbol == event.symbol
            and state.key.contract == event.contract
            and (event.meta.owner_id is None or state.key.owner_id == event.meta.owner_id)
        ]
        return self._intents(states, event, "session_end")

    def on_bar(self, event: DomainEvent) -> list[OrderIntent] | None:
        if not isinstance(event, BarClosedEvent):
            raise TypeError("PositionLiquidator.on_bar requires BarClosedEvent")
        states = [
            state
            for state in self.ledger.open_positions()
            if state.key.symbol == event.symbol and state.key.contract != event.contract
        ]
        return self._intents(states, event, "contract_roll")

    def on_fill(self, event: DomainEvent) -> None:
        if not isinstance(event, FillEvent):
            raise TypeError("PositionLiquidator.on_fill requires FillEvent")
        if event.purpose != "liquidation" or not event.meta.owner_id:
            return None
        self._pending.discard(
            PositionKey(
                event.meta.owner_id,
                event.strategy_id,
                event.strategy_version,
                event.symbol,
                event.contract,
            )
        )
        return None
