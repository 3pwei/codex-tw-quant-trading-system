from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import date

from ..market_data.settings import MarketDataSettings, normalize_provider


def _split_origins(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _split_emails(value: str) -> tuple[str, ...]:
    return tuple(
        item.strip().casefold() for item in value.split(",") if item.strip()
    )


@dataclass(frozen=True, init=False)
class LiveSettings:
    """Application settings composed with an isolated market-data config.

    Legacy keyword arguments and properties remain available so deployed
    environments and external callers can migrate without a flag day.
    """

    market_data: MarketDataSettings
    db_path: str
    heartbeat_seconds: float
    allowed_origins: tuple[str, ...]
    holidays: frozenset[date]
    access_mode: str
    cloudflare_access_team_domain: str | None
    cloudflare_access_audience: str | None
    authorization_mode: str
    bootstrap_admin_emails: tuple[str, ...]

    def __init__(
        self,
        *,
        market_data: MarketDataSettings | None = None,
        db_path: str = "output/live_market.sqlite3",
        heartbeat_seconds: float = 5.0,
        allowed_origins: tuple[str, ...] = ("http://localhost:3000",),
        holidays: frozenset[date] = frozenset(),
        access_mode: str = "disabled",
        cloudflare_access_team_domain: str | None = None,
        cloudflare_access_audience: str | None = None,
        authorization_mode: str = "disabled",
        bootstrap_admin_emails: tuple[str, ...] = (),
        # Compatibility inputs from the pre-provider settings model.
        mode: str | None = None,
        symbol: str | None = None,
        contract: str | None = None,
        replay_csv: str | None = None,
        replay_speed: float | None = None,
        history_days: int | None = None,
        history_limit: int | None = None,
        shioaji_api_key: str | None = None,
        shioaji_secret_key: str | None = None,
        shioaji_production: bool | None = None,
    ):
        data = market_data or MarketDataSettings()
        overrides: dict[str, object] = {}
        if mode is not None:
            overrides["provider"] = normalize_provider(mode)
        for name, value in (
            ("symbol", symbol),
            ("contract", contract),
            ("replay_csv", replay_csv),
            ("replay_speed", replay_speed),
            ("history_days", history_days),
            ("history_limit", history_limit),
            ("shioaji_api_key", shioaji_api_key),
            ("shioaji_secret_key", shioaji_secret_key),
            ("shioaji_production", shioaji_production),
        ):
            if value is not None:
                overrides[name] = value
        if overrides:
            data = replace(data, **overrides)
        object.__setattr__(self, "market_data", data)
        object.__setattr__(self, "db_path", db_path)
        object.__setattr__(self, "heartbeat_seconds", heartbeat_seconds)
        object.__setattr__(self, "allowed_origins", allowed_origins)
        object.__setattr__(self, "holidays", holidays)
        object.__setattr__(self, "access_mode", access_mode)
        object.__setattr__(
            self, "cloudflare_access_team_domain", cloudflare_access_team_domain
        )
        object.__setattr__(
            self, "cloudflare_access_audience", cloudflare_access_audience
        )
        object.__setattr__(self, "authorization_mode", authorization_mode)
        object.__setattr__(
            self, "bootstrap_admin_emails", bootstrap_admin_emails
        )

    @classmethod
    def from_env(cls) -> "LiveSettings":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        access_mode = os.getenv("MARKET_ACCESS_MODE", "disabled").lower().strip()
        return cls(
            market_data=MarketDataSettings.from_env(),
            db_path=os.getenv("MARKET_DB_PATH", "output/live_market.sqlite3"),
            heartbeat_seconds=float(os.getenv("MARKET_HEARTBEAT_SECONDS", "5")),
            allowed_origins=_split_origins(
                os.getenv("MARKET_ALLOWED_ORIGINS", "http://localhost:3000")
            ),
            holidays=frozenset(
                date.fromisoformat(item.strip())
                for item in os.getenv("MARKET_HOLIDAYS", "").split(",")
                if item.strip()
            ),
            access_mode=access_mode,
            cloudflare_access_team_domain=os.getenv("CF_ACCESS_TEAM_DOMAIN"),
            cloudflare_access_audience=os.getenv("CF_ACCESS_AUD"),
            authorization_mode=os.getenv(
                "PLATFORM_AUTHORIZATION_MODE", "disabled"
            ).lower().strip(),
            bootstrap_admin_emails=_split_emails(
                os.getenv("PLATFORM_BOOTSTRAP_ADMIN_EMAILS", "")
            ),
        )

    def validate(self) -> None:
        self.market_data.validate()
        if self.heartbeat_seconds <= 0:
            raise ValueError("MARKET_HEARTBEAT_SECONDS must be positive")
        if self.access_mode not in {"disabled", "cloudflare"}:
            raise ValueError("MARKET_ACCESS_MODE must be disabled or cloudflare")
        if self.access_mode == "cloudflare" and not (
            self.cloudflare_access_team_domain and self.cloudflare_access_audience
        ):
            raise ValueError(
                "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are required in cloudflare mode"
            )
        if self.authorization_mode not in {"disabled", "enforced"}:
            raise ValueError(
                "PLATFORM_AUTHORIZATION_MODE must be disabled or enforced"
            )
        if self.authorization_mode == "enforced" and not (
            self.bootstrap_admin_emails
        ):
            raise ValueError(
                "PLATFORM_BOOTSTRAP_ADMIN_EMAILS is required when platform "
                "authorization is enforced"
            )

    # Compatibility properties. New code should use ``settings.market_data``.
    @property
    def mode(self) -> str:
        return (
            "mock"
            if self.market_data.provider == "replay"
            else self.market_data.provider
        )

    @property
    def symbol(self) -> str:
        return self.market_data.symbol

    @property
    def contract(self) -> str:
        return self.market_data.contract

    @property
    def replay_csv(self) -> str:
        return self.market_data.replay_csv

    @property
    def replay_speed(self) -> float:
        return self.market_data.replay_speed

    @property
    def history_days(self) -> int:
        return self.market_data.history_days

    @property
    def history_limit(self) -> int:
        return self.market_data.history_limit

    @property
    def shioaji_api_key(self) -> str | None:
        return self.market_data.shioaji_api_key

    @property
    def shioaji_secret_key(self) -> str | None:
        return self.market_data.shioaji_secret_key

    @property
    def shioaji_production(self) -> bool:
        return self.market_data.shioaji_production
