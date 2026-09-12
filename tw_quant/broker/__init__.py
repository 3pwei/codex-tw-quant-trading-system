"""Broker-account boundaries, intentionally separate from market data."""

from .audit import (
    BrokerEventAuditRecord,
    BrokerEventAuditStatus,
    BrokerEventAuditStore,
    SQLiteBrokerEventAuditRepository,
)
from .callback_consumer import BrokerCallbackConsumer
from .capabilities import BrokerCapabilities
from .disabled import DisabledBroker, LockedBroker
from .events import BrokerEvent, broker_event_id
from .factory import BrokerAdapterFactory, build_broker
from .identity import BrokerAccountRef
from .instruments import (
    BrokerInstrumentMapper,
    CanonicalInstrument,
    LockedInstrumentMapper,
)
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
from .registry import (
    BrokerRegistration,
    BrokerRegistry,
    BrokerRuntimeState,
)
from .routing import RoutedBrokerOrder, RoutedBrokerOrderRequest
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
    "BrokerAdapterFactory",
    "BrokerAccount",
    "BrokerAccountRef",
    "BrokerAccountSafety",
    "BrokerAccountSnapshot",
    "BrokerCapabilities",
    "BrokerCallbackConsumer",
    "BrokerEvent",
    "BrokerEventAuditRecord",
    "BrokerEventAuditStatus",
    "BrokerEventAuditStore",
    "BrokerFillSnapshot",
    "BrokerInstrumentMapper",
    "BrokerOrder",
    "BrokerOrderRequest",
    "BrokerOrderStatus",
    "BrokerOrderSnapshot",
    "BrokerPositionSnapshot",
    "BrokerPort",
    "CompositeOrderAdmissionGate",
    "BrokerReconciliationSnapshot",
    "BrokerReconciliationSource",
    "BrokerRegistration",
    "BrokerRegistry",
    "BrokerRuntimeState",
    "BrokerConnectionSettings",
    "BrokerSettings",
    "BrokerSecretMaterial",
    "BrokerSecretProvider",
    "CanonicalInstrument",
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
    "LockedBroker",
    "LockedInstrumentMapper",
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
    "RoutedBrokerOrder",
    "RoutedBrokerOrderRequest",
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
