"""Broker-account boundaries, intentionally separate from market data."""

from .disabled import DisabledBroker
from .factory import build_broker
from .lifecycle import InvalidOrderTransition, transition_order
from .manager import LiveOrderManager
from .models import (
    BrokerAccountSnapshot,
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    ExecutionMode,
    OrderPurpose,
    canonical_paper_status,
)
from .ports import BrokerAccount, BrokerPort, LiveOrderStore, OrderExecutor
from .repository import SQLiteLiveOrderRepository
from .settings import BrokerSettings
from .shioaji import (
    ExternalOrderReport,
    LiveTradingSafety,
    ShioajiBrokerAdapter,
    ShioajiExecutionClient,
)
from .shioaji_simulation import (
    ShioajiCallbackBridge,
    ShioajiCallbackEvent,
    ShioajiSimulationExecutionClient,
    normalize_callback,
    normalize_trade,
)

__all__ = [
    "BrokerAccount",
    "BrokerAccountSnapshot",
    "BrokerOrder",
    "BrokerOrderRequest",
    "BrokerOrderStatus",
    "BrokerPort",
    "BrokerSettings",
    "DisabledBroker",
    "ExecutionMode",
    "ExternalOrderReport",
    "InvalidOrderTransition",
    "LiveTradingSafety",
    "LiveOrderManager",
    "LiveOrderStore",
    "OrderExecutor",
    "OrderPurpose",
    "ShioajiBrokerAdapter",
    "ShioajiCallbackBridge",
    "ShioajiCallbackEvent",
    "ShioajiExecutionClient",
    "ShioajiSimulationExecutionClient",
    "SQLiteLiveOrderRepository",
    "canonical_paper_status",
    "normalize_callback",
    "normalize_trade",
    "transition_order",
    "build_broker",
]
