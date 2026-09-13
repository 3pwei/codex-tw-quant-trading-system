from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

from ..broker.identity import BrokerAccountRef
from ..broker.settings import BrokerConnectionSettings


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ExecutionServiceSettings:
    """Non-secret settings owned only by the dedicated execution process."""

    broker_name: str = "disabled"
    connection_id: str = "primary"
    account_id: str = ""
    secret_ref: str = "environment:primary"
    live_trading_enabled: bool = False
    production_read_only_enabled: bool = False
    read_only_confirmation: str = ""
    instrument_map_json: str = ""
    confirmation: str = ""
    allowed_account_ids: frozenset[str] = frozenset()
    database_path: str = "/data/live_market.sqlite3"
    health_path: str = "/run/tw-quant-execution/health.json"
    heartbeat_seconds: float = 5.0
    reconciliation_interval_seconds: float = 45.0
    reconciliation_timeout_seconds: float = 20.0
    reconciliation_stale_seconds: float = 120.0
    callback_queue_size: int = 1024
    shutdown_drain_seconds: float = 5.0

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "ExecutionServiceSettings":
        values = os.environ if env is None else env
        allowed = frozenset(
            item.strip()
            for item in values.get("LIVE_ALLOWED_ACCOUNT_IDS", "").split(",")
            if item.strip()
        )
        return cls(
            broker_name=values.get("BROKER_PROVIDER", "disabled").strip().lower(),
            connection_id=values.get(
                "LIVE_BROKER_CONNECTION_ID", "primary"
            ).strip(),
            account_id=values.get("LIVE_BROKER_ACCOUNT_ID", "").strip(),
            secret_ref=values.get(
                "LIVE_BROKER_SECRET_REF", "environment:primary"
            ).strip(),
            live_trading_enabled=_enabled(values.get("LIVE_TRADING_ENABLED")),
            production_read_only_enabled=_enabled(
                values.get("LIVE_BROKER_READ_ONLY_ENABLED")
            ),
            read_only_confirmation=values.get(
                "LIVE_BROKER_READ_ONLY_CONFIRMATION", ""
            ).strip(),
            instrument_map_json=values.get(
                "LIVE_BROKER_INSTRUMENT_MAP_JSON", ""
            ).strip(),
            confirmation=values.get("LIVE_TRADING_CONFIRMATION", "").strip(),
            allowed_account_ids=allowed,
            database_path=values.get(
                "LIVE_EXECUTION_DB_PATH", "/data/live_market.sqlite3"
            ).strip(),
            health_path=values.get(
                "LIVE_EXECUTION_HEALTH_PATH",
                "/run/tw-quant-execution/health.json",
            ).strip(),
            heartbeat_seconds=float(
                values.get("LIVE_EXECUTION_HEARTBEAT_SECONDS", "5")
            ),
            reconciliation_interval_seconds=float(
                values.get("LIVE_RECONCILIATION_INTERVAL_SECONDS", "45")
            ),
            reconciliation_timeout_seconds=float(
                values.get("LIVE_RECONCILIATION_TIMEOUT_SECONDS", "20")
            ),
            reconciliation_stale_seconds=float(
                values.get("LIVE_RECONCILIATION_STALE_SECONDS", "120")
            ),
            callback_queue_size=int(
                values.get("LIVE_CALLBACK_QUEUE_SIZE", "1024")
            ),
            shutdown_drain_seconds=float(
                values.get("LIVE_CALLBACK_SHUTDOWN_DRAIN_SECONDS", "5")
            ),
        )

    @property
    def provider(self) -> str:
        """Backward-compatible name for the legacy BROKER_PROVIDER input."""

        return self.broker_name

    @property
    def connection(self) -> BrokerConnectionSettings:
        return BrokerConnectionSettings(
            connection_id=self.connection_id,
            broker_name=self.broker_name,
            account_id=self.account_id,
            enabled=self.live_trading_enabled or self.production_read_only_enabled,
            secret_ref=self.secret_ref,
        )

    @property
    def allowed_accounts(self) -> frozenset[BrokerAccountRef]:
        if not self.broker_name:
            return frozenset()
        return frozenset(
            BrokerAccountRef(self.broker_name, account_id)
            for account_id in self.allowed_account_ids
        )

    def validation_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if not self.broker_name:
            issues.append("missing_broker_name")
        if not self.connection_id:
            issues.append("missing_broker_connection_id")
        if not self.secret_ref:
            issues.append("missing_broker_secret_ref")
        connection_enabled = (
            self.live_trading_enabled or self.production_read_only_enabled
        )
        if connection_enabled and not self.account_id:
            issues.append("missing_account_id")
        if connection_enabled and not self.allowed_account_ids:
            issues.append("missing_account_allowlist")
        if (
            connection_enabled
            and self.account_id
            and self.account_id not in self.allowed_account_ids
        ):
            issues.append("account_not_allowlisted")
        if self.live_trading_enabled and self.production_read_only_enabled:
            issues.append("read_only_conflicts_with_live_trading")
        if self.production_read_only_enabled and self.broker_name != "shioaji":
            issues.append("unsupported_read_only_broker")
        if (
            self.production_read_only_enabled
            and self.read_only_confirmation != "I_UNDERSTAND_PRODUCTION_READ_ONLY"
        ):
            issues.append("invalid_read_only_confirmation")
        if self.production_read_only_enabled and not self.instrument_map_json:
            issues.append("missing_broker_instrument_map")
        if not self.database_path:
            issues.append("missing_execution_database_path")
        if not self.health_path:
            issues.append("missing_execution_health_path")
        if self.heartbeat_seconds <= 0:
            issues.append("invalid_execution_heartbeat")
        if not 30 <= self.reconciliation_interval_seconds <= 3600:
            issues.append("invalid_reconciliation_interval")
        if not 1 <= self.reconciliation_timeout_seconds < self.reconciliation_interval_seconds:
            issues.append("invalid_reconciliation_timeout")
        if self.reconciliation_stale_seconds < self.reconciliation_interval_seconds * 2:
            issues.append("invalid_reconciliation_stale_threshold")
        if not 1 <= self.callback_queue_size <= 100_000:
            issues.append("invalid_callback_queue_size")
        if not 0.1 <= self.shutdown_drain_seconds <= 60:
            issues.append("invalid_callback_shutdown_drain")
        return tuple(issues)
