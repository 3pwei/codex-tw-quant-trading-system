from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Callable, Mapping, Protocol

from ..broker import (
    BrokerAccountSafety,
    BrokerAccountWorker,
    BrokerCallbackConsumer,
    BrokerCapabilities,
    BrokerConnectionSettings,
    BrokerRegistration,
    BrokerRegistry,
    BrokerRuntimeState,
    BrokerSecretMaterial,
    BrokerSecretProvider,
    CompositeOrderAdmissionGate,
    DisabledExecutionWorker,
    ExecutionSupervisor,
    ExecutionWorkerSettings,
    LockedBroker,
    LockedInstrumentMapper,
    LiveOrderManager,
    LiveReconciliationService,
    LIVE_TRADING_CONFIRMATION,
    LockedOrderAdmissionGate,
    SQLiteLiveOrderRepository,
    SQLiteBrokerEventAuditRepository,
    SQLiteRecoveryLockRepository,
    SHIOAJI_READ_ONLY_CAPABILITIES,
    ShioajiBrokerAdapter,
    ShioajiInstrumentMapper,
    ShioajiProductionExecutionClient,
    LiveTradingSafety,
)
from ..broker.recovery import RecoveryOrderGate
from ..broker.secret_factory import build_broker_secret_provider
from .config import ExecutionServiceSettings
from .health import BrokerConnectionHealth
from .redaction import SecretRedactionFilter, mask_account
from .secrets import SecretConfigurationError


LOGGER = logging.getLogger("tw_quant.execution_service")


class ServiceWorker(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def snapshot(self) -> dict[str, object]: ...


@dataclass
class ExecutionServiceRuntime:
    """Locked composition root; an attached broker client can only read."""

    settings: ExecutionServiceSettings
    connection: BrokerConnectionSettings | None
    worker: ServiceWorker
    issues: tuple[str, ...]
    manager: LiveOrderManager | None = None
    order_repository: SQLiteLiveOrderRepository | None = field(default=None, repr=False)
    recovery_repository: SQLiteRecoveryLockRepository | None = field(
        default=None, repr=False
    )
    audit_repository: SQLiteBrokerEventAuditRepository | None = field(
        default=None, repr=False
    )
    broker_registry: BrokerRegistry | None = field(default=None, repr=False)
    redaction_filter: SecretRedactionFilter | None = field(default=None, repr=False)
    read_only_client: ShioajiProductionExecutionClient | None = field(
        default=None, repr=False
    )
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    _started: bool = field(default=False, repr=False)

    @property
    def locked(self) -> bool:
        return True

    def public_health(self) -> dict[str, object]:
        account = self.connection.account_ref if self.connection is not None else None
        worker_health = self.worker.snapshot()
        account_health = (
            worker_health["broker_accounts"][0]
            if isinstance(worker_health.get("broker_accounts"), list)
            and worker_health["broker_accounts"]
            else None
        )
        client_health = (
            self.read_only_client.health_state()
            if self.read_only_client is not None
            else {}
        )
        summary = BrokerConnectionHealth(
            connection_id=(
                self.connection.connection_id
                if self.connection is not None
                else self.settings.connection_id
            ),
            broker_name=(
                self.connection.broker_name
                if self.connection is not None
                else self.settings.broker_name
            ),
            account=account,
            execution_state=str(
                (account_health or {}).get("status")
                or client_health.get("execution_state") or (
                "locked"
                if (
                    self.settings.live_trading_enabled
                    or self.settings.production_read_only_enabled
                )
                else "disabled"
            )),
            enabled=(
                self.settings.live_trading_enabled
                or self.settings.production_read_only_enabled
            ),
            locked=True,
            recovery_status=str(
                (account_health or {}).get("recovery_status") or "locked"
            ),
            connected=bool(
                (account_health or {}).get("broker_connected", False)
            ),
            ca_ready=bool((account_health or {}).get("ca_ready", False)),
            read_only=bool(client_health.get("read_only", False)),
            callback_registered=bool(
                client_health.get("callback_registered", False)
            ),
            last_broker_read_time=client_health.get(  # type: ignore[arg-type]
                "last_broker_read_time"
            ),
            last_callback_time=client_health.get("last_callback_time"),  # type: ignore[arg-type]
        ).to_public_dict()
        return {
            **summary,
            "ordering_enabled": False,
            "broker_accounts": worker_health.get("broker_accounts", []),
        }

    def state_document(self) -> dict[str, object]:
        return {
            **self.public_health(),
            "heartbeat_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "issue_codes": list(dict.fromkeys(self.issues)),
            "external_order_calls": 0,
            "external_cancel_calls": 0,
        }

    def write_health(self) -> None:
        target = Path(self.settings.health_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state_document(), sort_keys=True), encoding="utf-8"
        )
        temporary.replace(target)

    async def start(self) -> None:
        await self.worker.start()
        self._stop_event.clear()
        self._started = True
        self.write_health()

    async def serve(self) -> None:
        await self.start()
        LOGGER.info(
            "live execution service started broker=%s account=%s recovery=locked",
            self.settings.broker_name,
            mask_account(self.settings.account_id),
        )
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.settings.heartbeat_seconds
                )
            except asyncio.TimeoutError:
                self.write_health()

    async def close(self) -> None:
        self._stop_event.set()
        await self.worker.stop()
        if self._started:
            self.write_health()
            self._started = False
        if self.recovery_repository is not None:
            self.recovery_repository.close()
        if self.audit_repository is not None:
            self.audit_repository.close()
        if self.order_repository is not None:
            self.order_repository.close()
        if self.redaction_filter is not None:
            LOGGER.removeFilter(self.redaction_filter)


def build_execution_service(
    settings: ExecutionServiceSettings | None = None,
    *,
    env: Mapping[str, str] | None = None,
    secret_provider: BrokerSecretProvider | None = None,
    production_client_factory: Callable[..., ShioajiProductionExecutionClient] = (
        ShioajiProductionExecutionClient
    ),
) -> ExecutionServiceRuntime:
    """Compose a locked service; an optional production client remains read-only."""

    config = settings or ExecutionServiceSettings.from_env(env)
    issues = list(config.validation_issues())
    material: BrokerSecretMaterial | None = None
    try:
        connection = config.connection
    except ValueError:
        connection = None

    provider = secret_provider
    if config.broker_name == "disabled":
        issues.append("execution_disabled")
    elif connection is not None:
        if provider is None:
            try:
                provider = build_broker_secret_provider(connection, env=env)
            except SecretConfigurationError as exc:
                issues.extend(exc.issue_codes)
        connection_enabled = (
            config.live_trading_enabled or config.production_read_only_enabled
        )
        if not connection_enabled:
            issues.append("live_trading_disabled")
        elif provider is not None:
            try:
                material = provider.load(connection)
            except SecretConfigurationError as exc:
                issues.extend(exc.issue_codes)
        if (
            config.live_trading_enabled
            and config.confirmation != LIVE_TRADING_CONFIRMATION
        ):
            issues.append("invalid_live_trading_confirmation")

    manager = None
    orders = None
    recovery = None
    audit = None
    registry = None
    redactor = None
    read_only_client = None
    worker: ServiceWorker = DisabledExecutionWorker()
    account = connection.account_ref if connection is not None else None
    if material is not None and account is not None and not config.validation_issues():
        redactor = SecretRedactionFilter(
            material.redaction_values + (account.account_id,)
        )
        LOGGER.addFilter(redactor)
        orders = SQLiteLiveOrderRepository(config.database_path)
        audit = SQLiteBrokerEventAuditRepository(config.database_path)
        recovery = SQLiteRecoveryLockRepository(config.database_path)
        registry = BrokerRegistry()
        port = LockedBroker(account.broker_name)
        capabilities = BrokerCapabilities()
        mapper = LockedInstrumentMapper()
        if config.production_read_only_enabled:
            try:
                mapper = ShioajiInstrumentMapper.from_json(
                    config.instrument_map_json
                )
            except ValueError:
                issues.append("invalid_broker_instrument_map")
                mapper = LockedInstrumentMapper()
            if isinstance(mapper, LockedInstrumentMapper):
                material = None
        if config.production_read_only_enabled and material is not None:
            read_only_client = production_client_factory(
                account_ref=account,
                allowed_accounts=config.allowed_accounts,
                secret_material=material,
                instrument_mapper=mapper,
                callback_queue_size=config.callback_queue_size,
            )
            port = ShioajiBrokerAdapter(
                read_only_client,
                LiveTradingSafety(account_id=account.account_id, enabled=False),
            )
            capabilities = SHIOAJI_READ_ONLY_CAPABILITIES
        registry.register(BrokerRegistration(
            account_ref=account,
            port=port,
            capabilities=capabilities,
            instrument_mapper=mapper,
            # READY means available for read/refresh. Order creation remains
            # blocked by the permanent admission gate below and the adapter's
            # read-only safety guard.
            state=(
                BrokerRuntimeState.READY
                if read_only_client is not None
                else BrokerRuntimeState.LOCKED
            ),
        ))
        registry.freeze()
        admission = CompositeOrderAdmissionGate((
            RecoveryOrderGate(recovery, account),
            BrokerAccountSafety(
                account=account,
                enabled=config.live_trading_enabled,
                confirmation=config.confirmation,
                allowed_accounts=config.allowed_accounts,
            ),
            LockedOrderAdmissionGate(),
        ))
        manager = LiveOrderManager(orders, registry, {account: admission})
        if read_only_client is not None and audit is not None:
            reconciliation = LiveReconciliationService(
                account_ref=account,
                order_store=orders,
                order_manager=manager,
                source=read_only_client,
                recovery_lock=recovery,
            )
            consumer = BrokerCallbackConsumer(audit, manager)
            account_worker = BrokerAccountWorker(
                account_ref=account,
                client=read_only_client,
                reconciliation=reconciliation,
                callback_consumer=consumer,
                recovery_lock=recovery,
                settings=ExecutionWorkerSettings(
                    reconciliation_interval_seconds=(
                        config.reconciliation_interval_seconds
                    ),
                    reconciliation_timeout_seconds=(
                        config.reconciliation_timeout_seconds
                    ),
                    snapshot_stale_seconds=config.reconciliation_stale_seconds,
                    heartbeat_seconds=config.heartbeat_seconds,
                    callback_queue_size=config.callback_queue_size,
                    shutdown_drain_seconds=config.shutdown_drain_seconds,
                ),
            )
            worker = ExecutionSupervisor((account_worker,))

    issues.append("production_submit_disabled")
    return ExecutionServiceRuntime(
        settings=config,
        connection=connection,
        worker=worker,
        issues=tuple(dict.fromkeys(issues)),
        manager=manager,
        order_repository=orders,
        recovery_repository=recovery,
        audit_repository=audit,
        broker_registry=registry,
        redaction_filter=redactor,
        read_only_client=read_only_client,
    )
