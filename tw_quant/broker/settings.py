from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Literal, Mapping


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BrokerSettings:
    provider: Literal["disabled", "shioaji"] = "disabled"
    live_trading_enabled: bool = False
    account_id: str = ""
    confirmation: str = ""
    allowed_account_ids: frozenset[str] = frozenset()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BrokerSettings":
        values = os.environ if env is None else env
        provider = values.get("BROKER_PROVIDER", "disabled").strip().lower()
        if provider not in {"disabled", "shioaji"}:
            raise ValueError(f"unsupported BROKER_PROVIDER: {provider}")
        allowed = frozenset(
            item.strip()
            for item in values.get("LIVE_ALLOWED_ACCOUNT_IDS", "").split(",")
            if item.strip()
        )
        return cls(
            provider=provider,  # type: ignore[arg-type]
            live_trading_enabled=_enabled(values.get("LIVE_TRADING_ENABLED")),
            account_id=values.get("LIVE_BROKER_ACCOUNT_ID", "").strip(),
            confirmation=values.get("LIVE_TRADING_CONFIRMATION", "").strip(),
            allowed_account_ids=allowed,
        )
