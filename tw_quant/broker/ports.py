from __future__ import annotations

from typing import Mapping, Protocol


class BrokerAccount(Protocol):
    """Account/position port. It must not be used as a quote source."""

    broker_name: str

    async def account_state(self) -> Mapping[str, object]: ...

    async def positions(self) -> list[Mapping[str, object]]: ...


class OrderExecutor(Protocol):
    """Future execution port, separate from strategy and market data."""

    async def submit_order(self, order: Mapping[str, object]) -> str: ...

    async def cancel_order(self, order_id: str) -> None: ...
