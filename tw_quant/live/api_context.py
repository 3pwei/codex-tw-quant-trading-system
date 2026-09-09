from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..auth import AccessIdentity, AccessTokenError, AccessValidator, AuthService, SQLiteAuthRepository
from ..paper import PaperTradingService
from ..replay import ReplaySessionNotFound, ReplayTradingSessionRegistry
from ..strategy import validate_composite_dependencies, validate_composite_definition
from .monitoring import HostResourceMonitor
from .rate_limit import SlidingWindowRateLimiter
from .service import LiveMarketService
from .settings import LiveSettings
from .storage import DEFAULT_OWNER_ID, BarRepository


@dataclass(frozen=True)
class ApiDependencies:
    config: LiveSettings
    repo: BarRepository
    validator: AccessValidator
    identity_repo: SQLiteAuthRepository
    auth_service: AuthService
    service: LiveMarketService
    paper: PaperTradingService
    replay_trading: ReplayTradingSessionRegistry
    host_monitor: HostResourceMonitor
    limiter: SlidingWindowRateLimiter

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

    def validated_composite_for_save(
        self, raw: dict[str, object], owner_id: str, strategy_id: str
    ) -> dict[str, object]:
        def resolve_child(
            child_id: str, child_version: int
        ) -> dict[str, object] | None:
            child = self.repo.composite_strategy(
                child_id, child_version, owner_user_id=owner_id
            )
            if child is None:
                return None
            if self.repo.composite_strategy_archived(child_id, owner_id):
                raise ValueError(
                    f"封存策略不可加入新的組合：{child['name']} v{child_version}"
                )
            return child

        definition = validate_composite_definition(
            raw,
            self.repo.strategy_parameters(owner_id),
            composite_resolver=resolve_child,
        )
        validate_composite_dependencies(definition, strategy_id)
        return definition

    def replay_session(self, session_id: str, request: Request):
        try:
            return self.replay_trading.get(
                session_id, self.request_owner_id(request)
            )
        except ReplaySessionNotFound as exc:
            raise ReplaySessionNotFound("找不到回放交易 Session") from exc
