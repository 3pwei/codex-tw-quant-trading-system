from __future__ import annotations

from typing import Protocol

from ..events import DomainEvent, EventMetadata, OrderIntent, RiskDecision


class RiskGate(Protocol):
    def on_order(self, event: DomainEvent) -> list[RiskDecision] | None: ...


class ResearchRiskGate:
    """Approve isolated historical intents that cannot reach a live broker."""

    def on_order(self, event: DomainEvent) -> list[RiskDecision]:
        if not isinstance(event, OrderIntent):
            raise TypeError("ResearchRiskGate.on_order requires OrderIntent")
        decision = RiskDecision(
            meta=EventMetadata.create(
                kind="risk_decision",
                occurred_at=event.meta.occurred_at,
                source="research_risk",
                source_key=event.order_id,
                causation_id=event.meta.event_id,
                correlation_id=event.meta.correlation_id,
                owner_id=event.meta.owner_id,
            ),
            order_id=event.order_id,
            approved=True,
            approved_quantity=event.quantity,
            reason="historical_research_approved",
        )
        return [decision]


class PassThroughRiskGate(ResearchRiskGate):
    """Backward-compatible alias for isolated execution tests."""


class DisabledRiskGate:
    """Fail closed for new exposure while still permitting risk reduction."""

    def on_order(self, event: DomainEvent) -> list[RiskDecision]:
        if not isinstance(event, OrderIntent):
            raise TypeError("DisabledRiskGate.on_order requires OrderIntent")
        approved = event.reduce_only
        reason = "risk_reducing_approved" if approved else "risk_gate_not_configured"
        return [
            RiskDecision(
                meta=EventMetadata.create(
                    kind="risk_decision",
                    occurred_at=event.meta.occurred_at,
                    source="disabled_risk",
                    source_key=event.order_id,
                    causation_id=event.meta.event_id,
                    correlation_id=event.meta.correlation_id,
                    owner_id=event.meta.owner_id,
                ),
                order_id=event.order_id,
                approved=approved,
                approved_quantity=event.quantity if approved else 0,
                reason=reason,
            )
        ]
