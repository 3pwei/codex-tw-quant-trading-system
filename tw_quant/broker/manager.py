from __future__ import annotations

from datetime import datetime, timezone

from .lifecycle import transition_order
from .models import BrokerOrder, BrokerOrderStatus
from .identity import BrokerAccountRef
from .ports import LiveOrderStore, OrderAdmissionGate
from .registry import BrokerRegistry
from .routing import RoutedBrokerOrderRequest


class LiveOrderManager:
    """Durable outbox coordinator. It never retries an ambiguous submission."""

    def __init__(
        self,
        repository: LiveOrderStore,
        registry: BrokerRegistry,
        admission_gates: dict[BrokerAccountRef, OrderAdmissionGate] | None = None,
    ):
        self.repository = repository
        self.registry = registry
        self.admission_gates = dict(admission_gates or {})
        self.interrupted_dispatches = repository.recover_interrupted_dispatches()

    def _assert_admitted(self, target: BrokerAccountRef) -> None:
        self.registry.resolve(target)
        if self.admission_gates:
            try:
                gate = self.admission_gates[target]
            except KeyError as exc:
                raise RuntimeError(
                    f"no admission gate configured for execution target: {target}"
                ) from exc
            gate.assert_ordering_allowed()

    def create(
        self, routed_request: RoutedBrokerOrderRequest
    ) -> tuple[BrokerOrder, bool]:
        self._assert_admitted(routed_request.target)
        return self.repository.reserve(routed_request)

    async def dispatch_once(self, target: BrokerAccountRef) -> BrokerOrder | None:
        self._assert_admitted(target)
        reserved = self.repository.claim_next(target)
        if reserved is None:
            return None
        if reserved.target != target:
            raise RuntimeError("durable outbox returned a different execution target")
        broker = self.registry.resolve(reserved.target)
        try:
            result = await broker.submit_order(reserved.order.request)
        except Exception:
            result = transition_order(
                reserved.order,
                BrokerOrderStatus.UNKNOWN,
                updated_at=datetime.now(timezone.utc),
                status_reason="broker_call_failed_reconciliation_required",
            )
        self.repository.finish_dispatch(result)
        return result

    async def reconcile(
        self, owner_id: str, client_order_id: str
    ) -> BrokerOrder:
        routed = self.repository.get_routed(owner_id, client_order_id)
        if routed is None:
            raise KeyError(f"unknown client order: {client_order_id}")
        broker = self.registry.resolve(routed.target)
        refreshed = await broker.refresh_order(routed.order)
        self.repository.save_reconciliation(refreshed)
        return refreshed

    async def reconcile_by_broker_order_id(
        self, target: BrokerAccountRef, broker_order_id: str
    ) -> BrokerOrder | None:
        """Refresh a callback-linked order without trusting callback state."""

        current = self.repository.get_by_broker_order_id(target, broker_order_id)
        if current is None:
            return None
        broker = self.registry.resolve(target)
        refreshed = await broker.refresh_order(current)
        self.repository.save_reconciliation(refreshed)
        return refreshed

    async def reconcile_broker_orders(
        self, target: BrokerAccountRef, owner_id: str | None = None
    ) -> list[BrokerOrder]:
        """Refresh orders that may have reached the broker, without resubmission."""

        reconciled: list[BrokerOrder] = []
        broker = self.registry.resolve(target)
        for current in self.repository.reconciliation_candidates(target, owner_id):
            try:
                refreshed = await broker.refresh_order(current)
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
