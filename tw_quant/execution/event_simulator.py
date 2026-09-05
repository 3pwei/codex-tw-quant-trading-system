from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ..events import (
    BarClosedEvent,
    DeterministicEventEngine,
    DomainEvent,
    EventMetadata,
    FillEvent,
    OrderIntent,
    PositionEvent,
    RiskDecision,
    SessionEvent,
    SignalEvent,
)
from ..futures_costs import FuturesCostConfig


OrderStatus = Literal["pending_risk", "approved", "rejected", "filled"]


class RiskGate(Protocol):
    def on_order(self, event: DomainEvent) -> list[RiskDecision] | None: ...


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


@dataclass
class OrderRecord:
    intent: OrderIntent
    status: OrderStatus = "pending_risk"
    approved_quantity: int = 0
    status_reason: str = "awaiting_risk"
    fill_id: str | None = None


class PositionLedger:
    """Owner-scoped futures positions with net realized and mark-to-market PnL."""

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


class PassThroughRiskGate:
    """Temporary simulation-only gate; account policies are added in PR #36."""

    def on_order(self, event: DomainEvent) -> list[RiskDecision]:
        if not isinstance(event, OrderIntent):
            raise TypeError("PassThroughRiskGate.on_order requires OrderIntent")
        decision = RiskDecision(
            meta=EventMetadata.create(
                kind="risk_decision",
                occurred_at=event.meta.occurred_at,
                source="pass_through_risk",
                source_key=event.order_id,
                causation_id=event.meta.event_id,
                correlation_id=event.meta.correlation_id,
                owner_id=event.meta.owner_id,
            ),
            order_id=event.order_id,
            approved=True,
            approved_quantity=event.quantity,
            reason="simulation_default_approved",
        )
        return [decision]


class SignalOrderRouter:
    def __init__(self, ledger: PositionLedger, *, default_quantity: int = 1):
        if default_quantity <= 0:
            raise ValueError("default_quantity must be positive")
        self.ledger = ledger
        self.default_quantity = default_quantity

    def on_signal(self, event: DomainEvent) -> list[OrderIntent] | None:
        if not isinstance(event, SignalEvent):
            raise TypeError("SignalOrderRouter.on_signal requires SignalEvent")
        owner_id = PositionLedger._owner(event)
        purpose: Literal["entry", "exit"] = "entry" if event.action == "enter" else "exit"
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
                purpose=purpose,
                reduce_only=reduce_only,
                reason=event.reason,
            )
        ]


class SimulatedBroker:
    """Deterministic market-order simulator; never calls an external broker."""

    def __init__(
        self,
        costs: FuturesCostConfig | None = None,
        position_ledger: PositionLedger | None = None,
    ):
        self.costs = costs or FuturesCostConfig()
        self.position_ledger = position_ledger
        self.orders: dict[str, OrderRecord] = {}
        self._order_sequence: list[str] = []
        self._last_close: dict[tuple[str, str], float] = {}
        self._bars_with_fills: set[str] = set()

    def on_order(self, event: DomainEvent) -> None:
        if not isinstance(event, OrderIntent):
            raise TypeError("SimulatedBroker.on_order requires OrderIntent")
        PositionLedger._owner(event)
        existing = self.orders.get(event.order_id)
        if existing is not None:
            if existing.intent != event:
                raise ValueError(f"conflicting duplicate order_id: {event.order_id}")
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
        if record.intent.execution_timing != "current_close":
            return None
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

    def bar_emitted_fill(self, event_id: str) -> bool:
        return event_id in self._bars_with_fills


class PositionLiquidator:
    def __init__(self, ledger: PositionLedger):
        self.ledger = ledger
        self._pending: set[PositionKey] = set()

    @staticmethod
    def _build_intent(state: PositionState, cause: DomainEvent, reason: str) -> OrderIntent:
        meta = EventMetadata.create(
            kind="order_intent",
            occurred_at=cause.meta.occurred_at,
            source="position_liquidator",
            source_key=f"{cause.meta.event_id}:{state.key.owner_id}:{state.key.strategy_id}:{state.key.contract}",
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
        )

    def _intents(
        self,
        states: list[PositionState],
        cause: DomainEvent,
        reason: str,
    ) -> list[OrderIntent] | None:
        orders: list[OrderIntent] = []
        for state in states:
            if state.key in self._pending:
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


class SimulatedExecutionPipeline:
    """Wire typed execution components into one deterministic event engine."""

    def __init__(
        self,
        *,
        costs: FuturesCostConfig | None = None,
        default_quantity: int = 1,
        risk_gate: RiskGate | None = None,
    ):
        resolved_costs = costs or FuturesCostConfig()
        self.ledger = PositionLedger(multiplier=resolved_costs.multiplier)
        self.broker = SimulatedBroker(resolved_costs, self.ledger)
        self.risk = risk_gate or PassThroughRiskGate()
        self.router = SignalOrderRouter(self.ledger, default_quantity=default_quantity)
        self.liquidator = PositionLiquidator(self.ledger)

    def install(self, engine: DeterministicEventEngine) -> None:
        engine.subscribe("signal", self.router.on_signal)
        engine.subscribe("order_intent", self.broker.on_order)
        engine.subscribe("order_intent", self.risk.on_order)
        engine.subscribe("risk_decision", self.broker.on_risk_decision)
        engine.subscribe("bar_closed", self.broker.on_bar)
        engine.subscribe("bar_closed", self._mark_after_bar)
        engine.subscribe("bar_closed", self.liquidator.on_bar)
        engine.subscribe("fill", self.ledger.on_fill)
        engine.subscribe("fill", self.liquidator.on_fill)
        engine.subscribe("session", self.liquidator.on_session)

    def _mark_after_bar(self, event: DomainEvent) -> list[PositionEvent] | None:
        if not isinstance(event, BarClosedEvent):
            raise TypeError("_mark_after_bar requires BarClosedEvent")
        if self.broker.bar_emitted_fill(event.meta.event_id):
            return None
        return self.ledger.mark_to_market(event)
