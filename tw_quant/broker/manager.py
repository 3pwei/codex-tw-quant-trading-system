from __future__ import annotations

from datetime import datetime, timezone

from .lifecycle import transition_order
from .models import BrokerOrder, BrokerOrderRequest, BrokerOrderStatus
from .ports import BrokerPort, LiveOrderStore, OrderAdmissionGate


class LiveOrderManager:
    """Durable outbox coordinator. It never retries an ambiguous submission."""

    def __init__(
        self,
        repository: LiveOrderStore,
        broker: BrokerPort,
        admission_gate: OrderAdmissionGate | None = None,
    ):
        self.repository = repository
        self.broker = broker
        self.admission_gate = admission_gate
        self.interrupted_dispatches = repository.recover_interrupted_dispatches()

    def create(self, request: BrokerOrderRequest) -> tuple[BrokerOrder, bool]:
        if self.admission_gate is not None:
            self.admission_gate.assert_ordering_allowed()
        return self.repository.reserve(request)

    async def dispatch_once(self) -> BrokerOrder | None:
        if self.admission_gate is not None:
            self.admission_gate.assert_ordering_allowed()
        reserved = self.repository.claim_next()
        if reserved is None:
            return None
        try:
            result = await self.broker.submit_order(reserved.request)
        except Exception:
            result = transition_order(
                reserved,
                BrokerOrderStatus.UNKNOWN,
                updated_at=datetime.now(timezone.utc),
                status_reason="broker_call_failed_reconciliation_required",
            )
        self.repository.finish_dispatch(result)
        return result

    async def reconcile(
        self, owner_id: str, client_order_id: str
    ) -> BrokerOrder:
        current = self.repository.get(owner_id, client_order_id)
        if current is None:
            raise KeyError(f"unknown client order: {client_order_id}")
        refreshed = await self.broker.refresh_order(current)
        self.repository.save_reconciliation(refreshed)
        return refreshed

    async def reconcile_by_broker_order_id(
        self, broker_order_id: str
    ) -> BrokerOrder | None:
        """Refresh a callback-linked order without trusting callback state."""

        current = self.repository.get_by_broker_order_id(broker_order_id)
        if current is None:
            return None
        refreshed = await self.broker.refresh_order(current)
        self.repository.save_reconciliation(refreshed)
        return refreshed

    async def reconcile_broker_orders(
        self, owner_id: str | None = None
    ) -> list[BrokerOrder]:
        """Refresh orders that may have reached the broker, without resubmission."""

        reconciled: list[BrokerOrder] = []
        for current in self.repository.reconciliation_candidates(owner_id):
            try:
                refreshed = await self.broker.refresh_order(current)
            except Exception:
                refreshed = transition_order(
                    current,
                    BrokerOrderStatus.UNKNOWN,
                    updated_at=datetime.now(timezone.utc),
                    status_reason="broker_reconciliation_failed",
                )
            self.repository.save_reconciliation(refreshed)
            reconciled.append(refreshed)
        return reconciled
