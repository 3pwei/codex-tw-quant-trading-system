from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Mapping

from ..broker import (
    BrokerAccountSafety,
    BrokerConnectionSettings,
    BrokerSecretMaterial,
    BrokerSecretProvider,
    CompositeOrderAdmissionGate,
    DisabledBroker,
    DisabledExecutionWorker,
    LiveOrderManager,
    LIVE_TRADING_CONFIRMATION,
    LockedOrderAdmissionGate,
    SQLiteLiveOrderRepository,
    SQLiteRecoveryLockRepository,
)
from ..broker.recovery import RecoveryOrderGate
from ..broker.secret_factory import build_broker_secret_provider
from .config import ExecutionServiceSettings
from .health import BrokerConnectionHealth
from .redaction import SecretRedactionFilter, mask_account
from .secrets import SecretConfigurationError


LOGGER = logging.getLogger("tw_quant.execution_service")


@dataclass
class ExecutionServiceRuntime:
    """Locked composition root. It intentionally has no broker submit client."""

    settings: ExecutionServiceSettings
    connection: BrokerConnectionSettings | None
    worker: DisabledExecutionWorker
    issues: tuple[str, ...]
    manager: LiveOrderManager | None = None
    order_repository: SQLiteLiveOrderRepository | None = field(default=None, repr=False)
    recovery_repository: SQLiteRecoveryLockRepository | None = field(
        default=None, repr=False
    )
    redaction_filter: SecretRedactionFilter | None = field(default=None, repr=False)
    _stop_event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    @property
    def locked(self) -> bool:
        return True

    def public_health(self) -> dict[str, object]:
        account = self.connection.account_ref if self.connection is not None else None
        return BrokerConnectionHealth(
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
            execution_state=(
                "locked" if self.settings.live_trading_enabled else "disabled"
            ),
            enabled=self.settings.live_trading_enabled,
            locked=True,
            recovery_status="locked",
        ).to_public_dict()

    def state_document(self) -> dict[str, object]:
        return {
            **self.public_health(),
            "heartbeat_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "issue_codes": list(self.issues),
            "external_order_calls": 0,
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
        if self.recovery_repository is not None:
            self.recovery_repository.close()
        if self.order_repository is not None:
            self.order_repository.close()
        if self.redaction_filter is not None:
            LOGGER.removeFilter(self.redaction_filter)


def build_execution_service(
    settings: ExecutionServiceSettings | None = None,
    *,
    env: Mapping[str, str] | None = None,
    secret_provider: BrokerSecretProvider | None = None,
) -> ExecutionServiceRuntime:
    """Compose a broker-neutral service without constructing a production SDK."""

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
        if not config.live_trading_enabled:
            issues.append("live_trading_disabled")
        elif provider is not None:
            try:
                material = provider.load(connection)
            except SecretConfigurationError as exc:
                issues.extend(exc.issue_codes)
        if config.confirmation != LIVE_TRADING_CONFIRMATION:
            issues.append("invalid_live_trading_confirmation")

    manager = None
    orders = None
    recovery = None
    redactor = None
    account = connection.account_ref if connection is not None else None
    if material is not None and account is not None and not config.validation_issues():
        redactor = SecretRedactionFilter(
            material.redaction_values + (account.account_id,)
        )
        LOGGER.addFilter(redactor)
        orders = SQLiteLiveOrderRepository(config.database_path)
        recovery = SQLiteRecoveryLockRepository(config.database_path)
        admission = CompositeOrderAdmissionGate((
            RecoveryOrderGate(recovery, account.broker_name, account.account_id),
            BrokerAccountSafety(
                account=account,
                enabled=config.live_trading_enabled,
                confirmation=config.confirmation,
                allowed_accounts=config.allowed_accounts,
            ),
            LockedOrderAdmissionGate(),
        ))
        manager = LiveOrderManager(orders, DisabledBroker(), admission)

    issues.append("production_submit_not_implemented")
    return ExecutionServiceRuntime(
        settings=config,
        connection=connection,
        worker=DisabledExecutionWorker(),
        issues=tuple(dict.fromkeys(issues)),
        manager=manager,
        order_repository=orders,
        recovery_repository=recovery,
        redaction_filter=redactor,
    )
