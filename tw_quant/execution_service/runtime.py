from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Mapping

from ..broker import (
    CompositeOrderAdmissionGate,
    DisabledBroker,
    DisabledExecutionWorker,
    LiveOrderManager,
    LiveTradingSafety,
    LockedOrderAdmissionGate,
    SQLiteLiveOrderRepository,
    SQLiteRecoveryLockRepository,
)
from ..broker.recovery import RecoveryOrderGate
from ..broker.shioaji import LIVE_CONFIRMATION
from .config import ExecutionServiceSettings
from .redaction import SecretRedactionFilter, mask_account
from .secrets import (
    EnvironmentLiveBrokerSecretLoader,
    LiveBrokerSecretLoader,
    LiveBrokerSecrets,
    SecretConfigurationError,
)


LOGGER = logging.getLogger("tw_quant.execution_service")


@dataclass
class ExecutionServiceRuntime:
    """Locked composition root. It intentionally has no broker submit client."""

    settings: ExecutionServiceSettings
    worker: DisabledExecutionWorker
    issues: tuple[str, ...]
    masked_account: str | None
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
        return {
            "enabled": self.settings.live_trading_enabled,
            "locked": True,
            "broker": self.settings.provider,
            "masked_account": self.masked_account,
            "recovery_status": "locked",
        }

    def state_document(self) -> dict[str, object]:
        return {
            **self.public_health(),
            "state": "locked" if self.settings.live_trading_enabled else "disabled",
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
        LOGGER.info("live execution service started in fail-closed mode")
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
    secret_loader: LiveBrokerSecretLoader | None = None,
) -> ExecutionServiceRuntime:
    """Compose a dedicated service without importing or constructing Shioaji SDK."""

    config = settings or ExecutionServiceSettings.from_env(env)
    issues = list(config.validation_issues())
    secrets: LiveBrokerSecrets | None = None
    values = os.environ if env is None else env
    account_id = values.get("LIVE_BROKER_ACCOUNT_ID")

    if config.provider == "disabled":
        issues.append("execution_disabled")
    elif config.provider == "shioaji":
        if not config.live_trading_enabled:
            issues.append("live_trading_disabled")
        else:
            try:
                secrets = (
                    secret_loader or EnvironmentLiveBrokerSecretLoader(env)
                ).load()
                account_id = secrets.account_id
            except SecretConfigurationError as exc:
                issues.extend(exc.issue_codes)
            if config.confirmation != LIVE_CONFIRMATION:
                issues.append("invalid_live_trading_confirmation")

    manager = None
    orders = None
    recovery = None
    redactor = None
    if secrets is not None and not config.validation_issues():
        redactor = SecretRedactionFilter((
            secrets.api_key,
            secrets.secret_key,
            secrets.ca_password,
            secrets.ca_cert_path,
            secrets.account_id,
        ))
        LOGGER.addFilter(redactor)
        orders = SQLiteLiveOrderRepository(config.database_path)
        recovery = SQLiteRecoveryLockRepository(config.database_path)
        admission = CompositeOrderAdmissionGate((
            RecoveryOrderGate(recovery, config.provider, secrets.account_id),
            LiveTradingSafety(
                account_id=secrets.account_id,
                enabled=config.live_trading_enabled,
                confirmation=config.confirmation,
                allowed_account_ids=secrets.allowed_account_ids,
            ),
            LockedOrderAdmissionGate(),
        ))
        manager = LiveOrderManager(orders, DisabledBroker(), admission)

    issues.append("production_submit_not_implemented")
    return ExecutionServiceRuntime(
        settings=config,
        worker=DisabledExecutionWorker(),
        issues=tuple(dict.fromkeys(issues)),
        masked_account=mask_account(account_id),
        manager=manager,
        order_repository=orders,
        recovery_repository=recovery,
        redaction_filter=redactor,
    )
