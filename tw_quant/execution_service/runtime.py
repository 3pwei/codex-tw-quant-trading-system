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
    CanaryBrokerAccountWorker,
    CanaryOrderAdmissionGate,
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
    SQLiteCanaryArmRepository,
    SQLiteExecutionTargetRepository,
    ExecutionTargetStatus,
    SQLiteLiveOrderRepository,
    SQLiteBrokerEventAuditRepository,
    SQLiteRecoveryLockRepository,
    SQLitePositionGuardianRepository,
    SQLiteBrokerTruthRepository,
    SHIOAJI_READ_ONLY_CAPABILITIES,
    SHIOAJI_CANARY_CAPABILITIES,
    ShioajiBrokerAdapter,
    ShioajiInstrumentMapper,
    ShioajiProductionExecutionClient,
    LiveTradingSafety,
    bootstrap_legacy_execution_target,
)
from ..broker.recovery import RecoveryOrderGate
from ..broker.secret_factory import build_broker_secret_provider
from .config import ExecutionServiceSettings
from .health import BrokerConnectionHealth
from .redaction import SecretRedactionFilter, mask_account
from .secrets import SecretConfigurationError
from ..live.shadow_store import SQLiteShadowExecutionRepository
from ..risk.live import LiveKillSwitchAction, LiveKillSwitchScope
from ..execution.guardian import (
    GuardianExecutionSink,
    LivePositionGuardian,
    LivePositionGuardianConfig,
    default_guardian_policies,
)
from ..execution.live_models import InstrumentSpec
from ..market import SQLiteExecutionQuoteRepository


LOGGER = logging.getLogger("tw_quant.execution_service")


class _GuardianKillSwitchView:
    def __init__(self, repository: SQLiteShadowExecutionRepository) -> None:
        self.repository = repository

    def actions(self, owner_id, target):
        account_scope = f"{target.broker_name}:{target.account_id}"
        states = self.repository.kill_switches({
            (LiveKillSwitchScope.GLOBAL.value, "global"),
            (LiveKillSwitchScope.OWNER.value, owner_id),
            (LiveKillSwitchScope.BROKER_ACCOUNT.value, account_scope),
        })
        return tuple(state.action for state in states)


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
    truth_repository: SQLiteBrokerTruthRepository | None = field(
        default=None, repr=False
    )
    canary_arm_repository: SQLiteCanaryArmRepository | None = field(default=None, repr=False)
    kill_switch_repository: SQLiteShadowExecutionRepository | None = field(default=None, repr=False)
    guardian_repository: SQLitePositionGuardianRepository | None = field(default=None, repr=False)
    quote_repository: SQLiteExecutionQuoteRepository | None = field(default=None, repr=False)
    execution_target_repository: SQLiteExecutionTargetRepository | None = field(
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
        return bool(self.public_health()["locked"])

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
        recovery_status = str(
            (account_health or {}).get("recovery_status") or "locked"
        )
        connected = bool((account_health or {}).get("broker_connected", False))
        ca_ready = bool((account_health or {}).get("ca_ready", False))
        arm_active = False
        if (
            self.settings.live_canary_enabled
            and self.canary_arm_repository is not None
            and account is not None
            and len(self.settings.live_canary_allowed_owner_ids) == 1
        ):
            owner_id = next(iter(self.settings.live_canary_allowed_owner_ids))
            arm_active = self.canary_arm_repository.active(
                owner_id, account, datetime.now(timezone.utc)
            ) is not None
        ordering_enabled = bool(
            self.settings.live_canary_enabled
            and arm_active
            and recovery_status == "ready"
            and connected
            and ca_ready
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
            locked=not ordering_enabled,
            recovery_status=recovery_status,
            connected=connected,
            ca_ready=ca_ready,
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
            "ordering_enabled": ordering_enabled,
            "broker_accounts": worker_health.get("broker_accounts", []),
        }

    def state_document(self) -> dict[str, object]:
        worker_health = self.worker.snapshot()
        return {
            **self.public_health(),
            "heartbeat_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "issue_codes": list(dict.fromkeys(self.issues)),
            "external_order_calls": int(worker_health.get("external_order_calls", 0) or 0),
            "external_cancel_calls": int(worker_health.get("external_cancel_calls", 0) or 0),
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
        if self.truth_repository is not None:
            self.truth_repository.close()
        if self.canary_arm_repository is not None:
            self.canary_arm_repository.close()
        if self.kill_switch_repository is not None:
            self.kill_switch_repository.close()
        if self.guardian_repository is not None:
            self.guardian_repository.close()
        if self.quote_repository is not None:
            self.quote_repository.close()
        if self.execution_target_repository is not None:
            self.execution_target_repository.close()
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
    truth = None
    canary_arms = None
    kill_switches = None
    guardian_store = None
    quote_store = None
    execution_targets = None
    registry = None
    redactor = None
    read_only_client = None
    worker: ServiceWorker = DisabledExecutionWorker()
    account = connection.account_ref if connection is not None else None
    if connection is not None and config.database_path:
        execution_targets = SQLiteExecutionTargetRepository(config.database_path)
        if account is not None and config.live_canary_allowed_owner_ids:
            try:
                owned_target = bootstrap_legacy_execution_target(
                    execution_targets,
                    owner_user_ids=config.live_canary_allowed_owner_ids,
                    connection=connection,
                )
                account = owned_target.account_ref
                if owned_target.status is not ExecutionTargetStatus.ACTIVE:
                    issues.append("execution_target_not_active")
                    material = None
            except (PermissionError, ValueError):
                issues.append("legacy_execution_target_bootstrap_rejected")
                material = None
    if material is not None and account is not None and not config.validation_issues():
        redactor = SecretRedactionFilter(
            material.redaction_values + (account.account_id,)
        )
        LOGGER.addFilter(redactor)
        orders = SQLiteLiveOrderRepository(config.database_path)
        audit = SQLiteBrokerEventAuditRepository(config.database_path)
        recovery = SQLiteRecoveryLockRepository(config.database_path)
        truth = SQLiteBrokerTruthRepository(config.database_path)
        registry = BrokerRegistry()
        port = LockedBroker(account.broker_name)
        capabilities = BrokerCapabilities()
        mapper = LockedInstrumentMapper()
        if config.production_read_only_enabled or config.live_canary_enabled:
            try:
                mapper = ShioajiInstrumentMapper.from_json(
                    config.instrument_map_json
                )
            except ValueError:
                issues.append("invalid_broker_instrument_map")
                mapper = LockedInstrumentMapper()
            if isinstance(mapper, LockedInstrumentMapper):
                material = None
        if (config.production_read_only_enabled or config.live_canary_enabled) and material is not None:
            read_only_client = production_client_factory(
                account_ref=account,
                allowed_accounts=config.allowed_accounts,
                secret_material=material,
                instrument_mapper=mapper,
                callback_queue_size=config.callback_queue_size,
                effective_mode="canary" if config.live_canary_enabled else "read_only",
            )
            port = ShioajiBrokerAdapter(
                read_only_client,
                LiveTradingSafety(
                    account_id=account.account_id,
                    enabled=config.live_canary_enabled,
                    confirmation=config.confirmation,
                    allowed_account_ids=config.allowed_account_ids,
                ),
            )
            capabilities = (
                SHIOAJI_CANARY_CAPABILITIES
                if config.live_canary_enabled
                else SHIOAJI_READ_ONLY_CAPABILITIES
            )
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
        gates = [
            RecoveryOrderGate(recovery, account),
            BrokerAccountSafety(
                account=account,
                enabled=config.live_trading_enabled,
                confirmation=config.confirmation,
                allowed_accounts=config.allowed_accounts,
            ),
        ]
        if config.live_canary_enabled and read_only_client is not None:
            canary_arms = SQLiteCanaryArmRepository(config.database_path)
            kill_switches = SQLiteShadowExecutionRepository(config.database_path)
            canary_config = config.canary_config
            def kill_switch_blocks(owner_id: str, reduce_only: bool) -> bool:
                account_scope = f"{account.broker_name}:{account.account_id}"
                states = kill_switches.kill_switches({
                    (LiveKillSwitchScope.GLOBAL.value, "global"),
                    (LiveKillSwitchScope.OWNER.value, owner_id),
                    (LiveKillSwitchScope.BROKER_ACCOUNT.value, account_scope),
                })
                return any(
                    state.action in {LiveKillSwitchAction.CANCEL_WORKING, LiveKillSwitchAction.FLATTEN}
                    or (state.action is LiveKillSwitchAction.HALT_ENTRY and not reduce_only)
                    for state in states
                )
            gates.append(CanaryOrderAdmissionGate(
                config=canary_config,
                arms=canary_arms,
                target=account,
                assert_recovery_ready=lambda: recovery.assert_ready(
                    account.broker_name, account.account_id
                ),
                readiness=lambda: {
                    **dict(read_only_client.health_state()),
                    "unknown_orders": sum(
                        order.status.value == "unknown"
                        for order in orders.orders(target=account)
                    ),
                },
                kill_switch_blocks=kill_switch_blocks,
                now=lambda: datetime.now(timezone.utc),
                broker_position=lambda contract: (
                    sum(
                        item.quantity
                        for item in snapshot.positions
                        if item.contract == contract
                    )
                    if (snapshot := truth.get(account)) is not None
                    else None
                ),
            ))
            orders.block_pending_dispatches(
                account, "restart_requires_operator_rearm_and_new_request"
            )
        else:
            gates.append(LockedOrderAdmissionGate())
        admission = CompositeOrderAdmissionGate(tuple(gates))
        manager = LiveOrderManager(orders, registry, {account: admission})
        if read_only_client is not None and audit is not None:
            reconciliation = LiveReconciliationService(
                account_ref=account,
                order_store=orders,
                order_manager=manager,
                source=read_only_client,
                recovery_lock=recovery,
                truth_store=truth,
            )
            consumer = BrokerCallbackConsumer(audit, manager)
            worker_type = (
                CanaryBrokerAccountWorker
                if config.live_canary_enabled
                else BrokerAccountWorker
            )
            worker_kwargs = (
                {"order_manager": manager} if config.live_canary_enabled else {}
            )
            if config.live_position_guardian_enabled:
                guardian_store = SQLitePositionGuardianRepository(config.database_path)
                quote_store = SQLiteExecutionQuoteRepository(config.database_path)
                guardian_config = LivePositionGuardianConfig(
                    enabled=True,
                    stop_loss_ticks=config.live_guardian_stop_loss_ticks,
                    take_profit_ticks=config.live_guardian_take_profit_ticks,
                    quote_stale_seconds=config.live_guardian_quote_stale_seconds,
                    poll_seconds=config.live_guardian_poll_seconds,
                )
                normal_policy, emergency_policy = default_guardian_policies(guardian_config)
                guardian = LivePositionGuardian(
                    account_ref=account,
                    config=guardian_config,
                    store=guardian_store,
                    order_store=orders,
                    truth_store=truth,
                    recovery=recovery,
                    registry=registry,
                    sink=GuardianExecutionSink(manager),
                    quotes=quote_store,
                    instrument=InstrumentSpec(
                        symbol=next(iter(config.live_canary_allowed_symbols)),
                        contract=next(iter(config.live_canary_allowed_contracts)),
                        tick_size=config.live_guardian_tick_size,
                        multiplier=config.live_guardian_multiplier,
                    ),
                    normal_policy=normal_policy,
                    emergency_policy=emergency_policy,
                    readiness=lambda: dict(read_only_client.health_state()),
                    kill_switches=_GuardianKillSwitchView(kill_switches),
                    owner_ids=config.live_canary_allowed_owner_ids,
                )
                worker_kwargs.update({
                    "position_guardian": guardian,
                    "guardian_poll_seconds": config.live_guardian_poll_seconds,
                })
            account_worker = worker_type(
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
                **worker_kwargs,
            )
            worker = ExecutionSupervisor((account_worker,))

    issues.append(
        "strategy_auto_live_disabled"
        if config.live_canary_enabled
        else "production_submit_disabled"
    )
    return ExecutionServiceRuntime(
        settings=config,
        connection=connection,
        worker=worker,
        issues=tuple(dict.fromkeys(issues)),
        manager=manager,
        order_repository=orders,
        recovery_repository=recovery,
        audit_repository=audit,
        truth_repository=truth,
        canary_arm_repository=canary_arms,
        kill_switch_repository=kill_switches,
        guardian_repository=guardian_store,
        quote_repository=quote_store,
        execution_target_repository=execution_targets,
        broker_registry=registry,
        redaction_filter=redactor,
        read_only_client=read_only_client,
    )
