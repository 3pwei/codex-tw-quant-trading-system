from __future__ import annotations

from datetime import datetime
from typing import Mapping, Protocol, runtime_checkable

from .models import BrokerOrder, BrokerOrderRequest


class BrokerAccount(Protocol):
    """Account/position port. It must not be used as a quote source."""

    broker_name: str

    async def account_state(self) -> Mapping[str, object]: ...

    async def positions(self) -> list[Mapping[str, object]]: ...


@runtime_checkable
class BrokerPort(BrokerAccount, Protocol):
    """Typed trading adapter boundary; it must never supply market quotes."""

    async def submit_order(self, order: BrokerOrderRequest) -> BrokerOrder: ...

    async def cancel_order(self, order: BrokerOrder) -> BrokerOrder: ...

    async def refresh_order(self, order: BrokerOrder) -> BrokerOrder: ...


class OrderExecutor(BrokerPort, Protocol):
    """Backward-compatible name for :class:`BrokerPort`."""


class LiveOrderStore(Protocol):
    """Persistence port used by the order manager and implemented by adapters."""

    def reserve(
        self, request: BrokerOrderRequest, *, occurred_at: datetime | None = None
    ) -> tuple[BrokerOrder, bool]: ...

    def get(self, owner_id: str, client_order_id: str) -> BrokerOrder | None: ...

    def claim_next(self) -> BrokerOrder | None: ...

    def finish_dispatch(self, order: BrokerOrder) -> None: ...

    def save_reconciliation(self, order: BrokerOrder) -> None: ...

    def recover_interrupted_dispatches(self) -> int: ...
