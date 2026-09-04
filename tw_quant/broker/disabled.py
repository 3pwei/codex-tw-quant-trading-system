from __future__ import annotations

from typing import Mapping


class DisabledBroker:
    """Safe default for the quote-only phase: every order is rejected."""

    broker_name = "disabled"

    async def account_state(self) -> Mapping[str, object]:
        return {"broker": self.broker_name, "trading_enabled": False}

    async def positions(self) -> list[Mapping[str, object]]:
        return []

    async def submit_order(self, order: Mapping[str, object]) -> str:
        raise RuntimeError("live order execution is disabled")

    async def cancel_order(self, order_id: str) -> None:
        raise RuntimeError("live order execution is disabled")
