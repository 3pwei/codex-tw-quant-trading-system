from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence, runtime_checkable

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


class OrderAdmissionGate(Protocol):
    """Application gate checked before a request is durably reserved."""

    def assert_ordering_allowed(self) -> None: ...


@dataclass(frozen=True)
class CompositeOrderAdmissionGate:
    """A single fail-closed admission point for every live order reservation."""

    gates: tuple[OrderAdmissionGate, ...]

    def __init__(self, gates: Sequence[OrderAdmissionGate]):
        if not gates:
            raise ValueError("at least one live order admission gate is required")
        object.__setattr__(self, "gates", tuple(gates))

    def assert_ordering_allowed(self) -> None:
        for gate in self.gates:
            gate.assert_ordering_allowed()


@dataclass(frozen=True)
class LockedOrderAdmissionGate:
    """Terminal gate used while production broker submission is unavailable."""

    reason: str = "production broker submission is not implemented"

    def assert_ordering_allowed(self) -> None:
        raise RuntimeError(self.reason)


class LiveOrderStore(Protocol):
    """Persistence port used by the order manager and implemented by adapters."""

    def reserve(
        self, request: BrokerOrderRequest, *, occurred_at: datetime | None = None
    ) -> tuple[BrokerOrder, bool]: ...

    def get(self, owner_id: str, client_order_id: str) -> BrokerOrder | None: ...

    def get_by_broker_order_id(self, broker_order_id: str) -> BrokerOrder | None: ...

    def orders(self, owner_id: str | None = None) -> list[BrokerOrder]: ...

    def reconciliation_candidates(
        self, owner_id: str | None = None
    ) -> list[BrokerOrder]: ...

    def claim_next(self) -> BrokerOrder | None: ...

    def finish_dispatch(self, order: BrokerOrder) -> None: ...

    def save_reconciliation(self, order: BrokerOrder) -> None: ...

    def recover_interrupted_dispatches(self) -> int: ...
