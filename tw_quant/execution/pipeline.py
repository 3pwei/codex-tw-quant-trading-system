from __future__ import annotations

from ..events import BarClosedEvent, DeterministicEventEngine, DomainEvent, PositionEvent
from ..futures_costs import FuturesCostConfig
from .liquidator import PositionLiquidator
from .position_ledger import PositionLedger
from .risk_gates import DisabledRiskGate, RiskGate
from .signal_router import ExecutionTiming, SignalOrderRouter
from .simulated_broker import SimulatedBroker


class SimulatedExecutionPipeline:
    """Compose deterministic execution components and their event subscriptions."""

    def __init__(
        self,
        *,
        costs: FuturesCostConfig | None = None,
        default_quantity: int = 1,
        execution_timing: ExecutionTiming = "next_bar_open",
        allow_signal_price_execution: bool = False,
        risk_gate: RiskGate | None = None,
        ledger: PositionLedger | None = None,
    ):
        resolved_costs = costs or FuturesCostConfig()
        self.ledger = ledger or PositionLedger(multiplier=resolved_costs.multiplier)
        self.broker = SimulatedBroker(
            resolved_costs,
            self.ledger,
            allow_signal_price_execution=allow_signal_price_execution,
        )
        self.risk = risk_gate or DisabledRiskGate()
        self.router = SignalOrderRouter(
            self.ledger,
            default_quantity=default_quantity,
            execution_timing=execution_timing,
        )
        self.liquidator = PositionLiquidator(
            self.ledger, self.broker.has_pending_reduce_only
        )

    def install(self, engine: DeterministicEventEngine) -> None:
        engine.subscribe("signal", self.router.on_signal)
        engine.subscribe("order_intent", self.broker.on_order)
        engine.subscribe("order_intent", self.risk.on_order)
        engine.subscribe("risk_decision", self.broker.on_risk_decision)
        risk_status_handler = getattr(self.risk, "on_order_status", None)
        if callable(risk_status_handler):
            engine.subscribe("order_status", risk_status_handler)
        engine.subscribe("bar_closed", self.broker.on_bar)
        engine.subscribe("bar_closed", self._mark_after_bar)
        engine.subscribe("bar_closed", self.liquidator.on_bar)
        engine.subscribe("fill", self.ledger.on_fill)
        engine.subscribe("fill", self.liquidator.on_fill)
        risk_fill_handler = getattr(self.risk, "on_fill", None)
        if callable(risk_fill_handler):
            engine.subscribe("fill", risk_fill_handler)
        engine.subscribe("session", self.broker.on_session)
        engine.subscribe("session", self.liquidator.on_session)
        risk_session_handler = getattr(self.risk, "on_session", None)
        if callable(risk_session_handler):
            engine.subscribe("session", risk_session_handler)

    def _mark_after_bar(self, event: DomainEvent) -> list[PositionEvent] | None:
        if not isinstance(event, BarClosedEvent):
            raise TypeError("_mark_after_bar requires BarClosedEvent")
        if self.broker.bar_emitted_fill(event.meta.event_id):
            return None
        return self.ledger.mark_to_market(event)
