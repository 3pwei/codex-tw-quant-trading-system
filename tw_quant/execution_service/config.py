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
    confirmation: str = ""
    allowed_account_ids: frozenset[str] = frozenset()
    database_path: str = "/data/live_market.sqlite3"
    health_path: str = "/run/tw-quant-execution/health.json"
    heartbeat_seconds: float = 5.0

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
            enabled=self.live_trading_enabled,
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
        if self.live_trading_enabled and not self.account_id:
            issues.append("missing_account_id")
        if self.live_trading_enabled and not self.allowed_account_ids:
            issues.append("missing_account_allowlist")
        if (
            self.live_trading_enabled
            and self.account_id
            and self.account_id not in self.allowed_account_ids
        ):
            issues.append("account_not_allowlisted")
        if not self.database_path:
            issues.append("missing_execution_database_path")
        if not self.health_path:
            issues.append("missing_execution_health_path")
        if self.heartbeat_seconds <= 0:
            issues.append("invalid_execution_heartbeat")
        return tuple(issues)
