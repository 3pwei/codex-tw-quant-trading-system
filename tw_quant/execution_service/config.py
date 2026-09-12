from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ExecutionServiceSettings:
    """Non-secret settings owned only by the dedicated execution process."""

    provider: str = "disabled"
    live_trading_enabled: bool = False
    confirmation: str = ""
    database_path: str = "/data/live_market.sqlite3"
    health_path: str = "/run/tw-quant-execution/health.json"
    heartbeat_seconds: float = 5.0

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "ExecutionServiceSettings":
        values = os.environ if env is None else env
        return cls(
            provider=values.get("BROKER_PROVIDER", "disabled").strip().lower(),
            live_trading_enabled=_enabled(values.get("LIVE_TRADING_ENABLED")),
            confirmation=values.get("LIVE_TRADING_CONFIRMATION", "").strip(),
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

    def validation_issues(self) -> tuple[str, ...]:
        issues: list[str] = []
        if self.provider not in {"disabled", "shioaji"}:
            issues.append("unknown_broker_provider")
        if not self.database_path:
            issues.append("missing_execution_database_path")
        if not self.health_path:
            issues.append("missing_execution_health_path")
        if self.heartbeat_seconds <= 0:
            issues.append("invalid_execution_heartbeat")
        return tuple(issues)
