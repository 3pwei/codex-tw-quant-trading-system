"""Broker-account boundaries, intentionally separate from market data."""

from .audit import (
    BrokerEventAuditRecord,
    BrokerEventAuditStatus,
    BrokerEventAuditStore,
    SQLiteBrokerEventAuditRepository,
)
from .callback_consumer import BrokerCallbackConsumer
from .disabled import DisabledBroker
from .events import BrokerEvent, broker_event_id
from .factory import build_broker
from .identity import BrokerAccountRef
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
from .ports import (
    BrokerAccount,
    BrokerPort,
    CompositeOrderAdmissionGate,
    LiveOrderStore,
    LockedOrderAdmissionGate,
    OrderAdmissionGate,
    OrderExecutor,
)
from .reconciliation import (
    BrokerFillSnapshot,
    BrokerOrderSnapshot,
    BrokerPositionSnapshot,
    BrokerReconciliationSnapshot,
    BrokerReconciliationSource,
    LiveReconciliationService,
    ReconciliationIssue,
    ReconciliationReport,
)
from .recovery import (
    RecoveryLockStore,
    RecoveryOrderGate,
    RecoveryState,
    RecoveryStatus,
    SQLiteRecoveryLockRepository,
)
from .repository import SQLiteLiveOrderRepository
from .secrets import (
    BrokerSecretMaterial,
    BrokerSecretProvider,
    SecretConfigurationError,
)
from .settings import BrokerConnectionSettings, BrokerSettings
from .safety import BrokerAccountSafety, LIVE_TRADING_CONFIRMATION
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
from .worker import (
    DisabledExecutionWorker,
    ExecutionRuntime,
    ExecutionWorker,
    ExecutionWorkerMonitor,
    ExecutionWorkerSettings,
    build_execution_runtime,
)

__all__ = [
    "BrokerAccount",
    "BrokerAccountRef",
    "BrokerAccountSafety",
    "BrokerAccountSnapshot",
    "BrokerCallbackConsumer",
    "BrokerEvent",
    "BrokerEventAuditRecord",
    "BrokerEventAuditStatus",
    "BrokerEventAuditStore",
    "BrokerFillSnapshot",
    "BrokerOrder",
    "BrokerOrderRequest",
    "BrokerOrderStatus",
    "BrokerOrderSnapshot",
    "BrokerPositionSnapshot",
    "BrokerPort",
    "CompositeOrderAdmissionGate",
    "BrokerReconciliationSnapshot",
    "BrokerReconciliationSource",
    "BrokerConnectionSettings",
    "BrokerSettings",
    "BrokerSecretMaterial",
    "BrokerSecretProvider",
    "DisabledBroker",
    "DisabledExecutionWorker",
    "ExecutionMode",
    "ExecutionRuntime",
    "ExecutionWorker",
    "ExecutionWorkerMonitor",
    "ExecutionWorkerSettings",
    "ExternalOrderReport",
    "InvalidOrderTransition",
    "LiveTradingSafety",
    "LIVE_TRADING_CONFIRMATION",
    "LiveOrderManager",
    "LiveOrderStore",
    "LockedOrderAdmissionGate",
    "LiveReconciliationService",
    "OrderAdmissionGate",
    "OrderExecutor",
    "OrderPurpose",
    "ReconciliationIssue",
    "ReconciliationReport",
    "RecoveryLockStore",
    "RecoveryOrderGate",
    "RecoveryState",
    "RecoveryStatus",
    "ShioajiBrokerAdapter",
    "ShioajiCallbackBridge",
    "ShioajiCallbackEvent",
    "ShioajiExecutionClient",
    "ShioajiSimulationExecutionClient",
    "SecretConfigurationError",
    "SQLiteBrokerEventAuditRepository",
    "SQLiteLiveOrderRepository",
    "SQLiteRecoveryLockRepository",
    "canonical_paper_status",
    "normalize_callback",
    "normalize_trade",
    "transition_order",
    "build_broker",
    "build_execution_runtime",
    "broker_event_id",
]
