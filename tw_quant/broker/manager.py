from __future__ import annotations

from datetime import datetime, timezone

from .lifecycle import transition_order
from .models import BrokerOrder, BrokerOrderRequest, BrokerOrderStatus
from .ports import BrokerPort, LiveOrderStore


class LiveOrderManager:
    """Durable outbox coordinator. It never retries an ambiguous submission."""

    def __init__(self, repository: LiveOrderStore, broker: BrokerPort):
        self.repository = repository
        self.broker = broker
        self.interrupted_dispatches = repository.recover_interrupted_dispatches()

    def create(self, request: BrokerOrderRequest) -> tuple[BrokerOrder, bool]:
        return self.repository.reserve(request)

    async def dispatch_once(self) -> BrokerOrder | None:
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
