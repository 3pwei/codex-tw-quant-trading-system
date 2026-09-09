from __future__ import annotations

from .disabled import DisabledBroker
from .ports import BrokerPort
from .settings import BrokerSettings
from .shioaji import LiveTradingSafety, ShioajiBrokerAdapter, ShioajiExecutionClient


def build_broker(
    settings: BrokerSettings,
    *,
    shioaji_client: ShioajiExecutionClient | None = None,
) -> BrokerPort:
    """Build the execution adapter; disabled is the unconditional default."""

    if settings.provider == "disabled" or not settings.live_trading_enabled:
        return DisabledBroker()
    if shioaji_client is None:
        raise ValueError("enabled Shioaji execution requires a client")
    return ShioajiBrokerAdapter(
        shioaji_client,
        LiveTradingSafety(
            account_id=settings.account_id,
            enabled=settings.live_trading_enabled,
            confirmation=settings.confirmation,
            allowed_account_ids=settings.allowed_account_ids,
        ),
    )
