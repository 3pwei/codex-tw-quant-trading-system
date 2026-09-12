from __future__ import annotations

from typing import Literal

from ..events import DomainEvent, EventMetadata, OrderIntent, SignalEvent
from .position_ledger import PositionLedger


ExecutionTiming = Literal[
    "next_bar_open", "current_close", "signal_price", "bar_trigger"
]


class SignalOrderRouter:
    """Translate strategy signals into position-aware market-order intents."""

    def __init__(
        self,
        ledger: PositionLedger,
        *,
        default_quantity: int = 1,
        execution_timing: ExecutionTiming = "next_bar_open",
    ):
        if default_quantity <= 0:
            raise ValueError("default_quantity must be positive")
        self.ledger = ledger
        self.default_quantity = default_quantity
        self.execution_timing = execution_timing

    def on_signal(self, event: DomainEvent) -> list[OrderIntent] | None:
        if not isinstance(event, SignalEvent):
            raise TypeError("SignalOrderRouter.on_signal requires SignalEvent")
        owner_id = PositionLedger._owner(event)
        purpose: Literal["entry", "exit"] = (
            "entry" if event.action == "enter" else "exit"
        )
        if purpose == "entry":
            side = "buy" if event.direction == "long" else "sell"
            quantity = self.default_quantity
            reduce_only = False
        else:
            state = self.ledger.state(
                owner_id,
                event.strategy_id,
                event.strategy_version,
                event.symbol,
                event.contract,
            )
            if state is None or state.quantity == 0:
                return None
            expected = "long" if state.quantity > 0 else "short"
            if expected != event.direction:
                raise ValueError("exit signal direction does not match the open position")
            side = "sell" if state.quantity > 0 else "buy"
            quantity = abs(state.quantity)
            reduce_only = True

        order_meta = EventMetadata.create(
            kind="order_intent",
            occurred_at=event.meta.occurred_at,
            source="signal_order_router",
            source_key=event.meta.event_id,
            causation_id=event.meta.event_id,
            correlation_id=event.meta.correlation_id,
            owner_id=owner_id,
        )
        return [
            OrderIntent(
                meta=order_meta,
                order_id=order_meta.event_id,
                strategy_id=event.strategy_id,
                strategy_version=event.strategy_version,
                symbol=event.symbol,
                contract=event.contract,
                side=side,
                quantity=quantity,
                execution_timing=self.execution_timing,
                purpose=purpose,
                reduce_only=reduce_only,
                reason=event.reason,
                reference_price=event.reference_price,
                stop_loss_price=event.stop_loss_price,
                trading_date=event.trading_date,
            )
        ]
