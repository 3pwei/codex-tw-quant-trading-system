from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..auth import AccessIdentity, AccessTokenError, AccessValidator, AuthService, SQLiteAuthRepository
from ..broker import ExecutionWorkerMonitor
from ..paper import PaperTradingService
from ..replay import ReplayTradingSessionRegistry
from .application import (
    PaperApplicationService,
    ResearchApplicationService,
    StrategyApplicationService,
)
from .monitoring import HostResourceMonitor
from .rate_limit import SlidingWindowRateLimiter
from .service import LiveMarketService
from .settings import LiveSettings
from .storage import DEFAULT_OWNER_ID, MarketRepository, StrategyRepository


@dataclass(frozen=True)
class ApiDependencies:
    config: LiveSettings
    market_repo: MarketRepository
    strategy_repo: StrategyRepository
    validator: AccessValidator
    identity_repo: SQLiteAuthRepository
    auth_service: AuthService
    service: LiveMarketService
    paper: PaperTradingService
    replay_trading: ReplayTradingSessionRegistry
    host_monitor: HostResourceMonitor
    limiter: SlidingWindowRateLimiter
    paper_app: PaperApplicationService
    research_app: ResearchApplicationService
    strategy_app: StrategyApplicationService
    execution_worker: ExecutionWorkerMonitor

    def identity_from_headers(self, headers) -> AccessIdentity | None:
        token = headers.get("cf-access-jwt-assertion")
        if token:
            return self.validator.authenticate(token)
        subject = headers.get("x-authenticated-subject")
        if subject:
            return AccessIdentity(
                subject=subject,
                email=headers.get("x-authenticated-email"),
            )
        if self.config.access_mode == "disabled":
            return None
        raise AccessTokenError("missing authenticated request identity")

    def user_from_headers(self, headers):
        identity = self.identity_from_headers(headers)
        return (
            self.auth_service.local_development_user()
            if identity is None
            else self.auth_service.identify(identity)
        )

    @staticmethod
    def public_market_status(status: dict[str, object]) -> dict[str, object]:
        return {
            key: status[key]
            for key in (
                "type", "service_status", "symbol", "contract", "connection_status",
                "last_tick_time", "last_bar_time", "last_heartbeat_time",
                "server_time", "latency_ms", "market_latency_seconds",
                "tick_age_ms", "tick_age_seconds", "bar_age_seconds",
                "stale_after_seconds", "trading_block_reason",
                "history_bars_loaded",
            )
        }

    @staticmethod
    def request_owner_id(request: Request) -> str:
        user = request.state.auth_user
        return user.user_id if user.registered else DEFAULT_OWNER_ID
