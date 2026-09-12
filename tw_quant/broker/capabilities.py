from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BrokerCapabilities:
    """Execution features declared by one broker adapter registration."""

    supports_market_orders: bool = False
    supports_limit_orders: bool = False
    supports_ioc: bool = False
    supports_fok: bool = False
    supports_cancel: bool = False
    supports_replace: bool = False
    supports_native_stop: bool = False
    supports_oco: bool = False
    supports_client_order_id: bool = False
    supports_order_callback: bool = False
    supports_fill_callback: bool = False
    supports_partial_fills: bool = False

    def require(self, capability: str) -> None:
        """Fail closed when an execution policy requests an unavailable feature."""

        if capability not in self.__dataclass_fields__:
            raise ValueError(f"unknown broker capability: {capability}")
        if not getattr(self, capability):
            raise RuntimeError(f"broker capability is unavailable: {capability}")
