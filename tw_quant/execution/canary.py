from __future__ import annotations

from dataclasses import dataclass

from ..broker import (
    BrokerAccountRef,
    BrokerOrder,
    BrokerOrderRequest,
    LiveOrderManager,
    RoutedBrokerOrderRequest,
)


@dataclass(frozen=True)
class LiveExecutionSink:
    """The only real sink: durable manager reservation, never direct broker I/O."""

    manager: LiveOrderManager

    def reserve(
        self, target: BrokerAccountRef, request: BrokerOrderRequest
    ) -> tuple[BrokerOrder, bool]:
        if request.source != "manual_live_canary":
            raise RuntimeError("real_execution_source_not_allowed")
        return self.manager.create(RoutedBrokerOrderRequest(target, request))

    def reserve_cancel(
        self, target: BrokerAccountRef, owner_id: str, client_order_id: str
    ) -> tuple[BrokerOrder, bool]:
        return self.manager.request_cancel(target, owner_id, client_order_id)
