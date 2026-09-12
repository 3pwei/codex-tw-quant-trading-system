from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..auth import (
    AccessValidator,
    AuthService,
    CloudflareAccessValidator,
    DisabledAccessValidator,
    SQLiteAuthRepository,
)
from ..broker import DisabledExecutionWorker
from ..market import TradingCalendar
from ..market_data import HistoricalMarketDataProvider, LiveMarketDataProvider, build_market_data_provider
from ..paper import PaperTradingService, SQLitePaperRepository
from ..replay import ReplayTradingSessionRegistry
from .api_context import ApiDependencies
from .application import (
    PaperApplicationService,
    PaperAutoExecutionController,
    ResearchApplicationService,
    StrategyApplicationService,
    TradingRuntimeApplicationService,
)
from .api_models import (
    AdminUserCreate,
    AdminUserUpdate,
    BacktestExecutionRequest,
    BacktestRunPurge,
    CompositeStrategyPurge,
    CompositeStrategyUpdate,
    PaperControlRequest,
    PaperOrderCreate,
    ReplayCursorUpdate,
    ReplayPrepareRequest,
    StrategyParametersUpdate,
    TradingRuntimeCreate,
)
from .api_routes import (
    build_admin_router,
    build_market_router,
    build_paper_router,
    build_research_router,
    build_strategy_router,
    build_system_router,
    build_trading_runtime_router,
)
from .api_routes.admin import system_status
from .api_security import (
    install_authorization_middleware,
    rate_limit_scope as _rate_limit_scope,
)
from .monitoring import HostResourceMonitor
from .rate_limit import RateLimitRule, SlidingWindowRateLimiter
from .request_limit import RequestBodyLimitMiddleware
from .service import LiveMarketService
from .settings import LiveSettings
from .storage import ApplicationRepository, SQLiteBarRepository

__all__ = [
    "AdminUserCreate", "AdminUserUpdate", "BacktestExecutionRequest",
    "BacktestRunPurge",
    "CompositeStrategyPurge", "CompositeStrategyUpdate", "PaperControlRequest",
    "PaperOrderCreate", "ReplayCursorUpdate", "ReplayPrepareRequest",
    "StrategyParametersUpdate", "_rate_limit_scope", "create_app", "system_status",
    "TradingRuntimeCreate",
]


def _build_access_validator(
    config: LiveSettings, validator: AccessValidator | None
) -> AccessValidator:
    if validator is not None:
        return validator
    if config.access_mode == "cloudflare":
        return CloudflareAccessValidator(
            config.cloudflare_access_team_domain or "",
            config.cloudflare_access_audience or "",
        )
    return DisabledAccessValidator()


def _build_rate_limiter(config: LiveSettings) -> SlidingWindowRateLimiter:
    return SlidingWindowRateLimiter(
        {
            "access_requests": RateLimitRule(
                config.rate_limit_access_requests_per_hour, 60 * 60
            ),
            "backtests": RateLimitRule(
                config.rate_limit_backtests_per_minute, 60
            ),
            "replay_prepares": RateLimitRule(
                config.rate_limit_replay_prepares_per_minute, 60
            ),
            "orders": RateLimitRule(
                config.rate_limit_orders_per_minute, 60
            ),
        }
    )


def create_app(
    settings: LiveSettings | None = None,
    feed: LiveMarketDataProvider | None = None,
    history_provider: HistoricalMarketDataProvider | None = None,
    repository: ApplicationRepository | None = None,
    access_validator: AccessValidator | None = None,
    auth_repository: SQLiteAuthRepository | None = None,
    rate_limiter: SlidingWindowRateLimiter | None = None,
) -> FastAPI:
    config = settings or LiveSettings.from_env()
    config.validate()
    repo = repository or SQLiteBarRepository(config.db_path)
    market_feed = feed or build_market_data_provider(config.market_data)
    if history_provider is None:
        capabilities = getattr(market_feed, "capabilities", None)
        if getattr(capabilities, "historical_bars", False):
            history_provider = market_feed

    validator = _build_access_validator(config, access_validator)
    identity_repo = auth_repository or SQLiteAuthRepository(config.db_path)
    identity_repo.bootstrap_admins(config.bootstrap_admin_emails)
    if config.bootstrap_admin_emails:
        bootstrap_owner = identity_repo.user_by_email(config.bootstrap_admin_emails[0])
        if bootstrap_owner is not None:
            repo.claim_legacy_ownership(bootstrap_owner.user_id)

    auth_service = AuthService(
        identity_repo, authorization_mode=config.authorization_mode
    )
    service = LiveMarketService(
        market_feed, repo, config.symbol, config.heartbeat_seconds,
        TradingCalendar(config.holidays), config.history_limit,
        history_provider=history_provider,
        stale_after_seconds=config.stale_after_seconds,
    )
    paper = PaperTradingService(SQLitePaperRepository(config.db_path))
    replay_trading = ReplayTradingSessionRegistry()
    limiter = rate_limiter or _build_rate_limiter(config)
    paper_app = PaperApplicationService(
        repo,
        paper,
        service,
        config.symbol,
        config.stale_after_seconds,
    )
    research_app = ResearchApplicationService(
        repo, repo, repo, replay_trading, config.symbol
    )
    strategy_app = StrategyApplicationService(repo, repo, config.symbol)
    runtime_app = TradingRuntimeApplicationService(
        repo, repo, repo, config.symbol, config.history_limit
    )
    paper_auto = PaperAutoExecutionController(
        repo, identity_repo, service, paper
    )
    paper_auto.recover()
    runtime_app.add_decision_listener(paper_auto.on_decision)
    service.add_bar_listener(paper_auto.before_bar)
    service.add_bar_listener(paper.on_bar)
    service.add_bar_listener(paper_auto.after_bar)
    service.add_bar_listener(runtime_app.on_bar)
    execution_worker = DisabledExecutionWorker()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await execution_worker.start()
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            await execution_worker.stop()
            service.remove_bar_listener(paper_auto.before_bar)
            service.remove_bar_listener(paper.on_bar)
            service.remove_bar_listener(paper_auto.after_bar)
            service.remove_bar_listener(runtime_app.on_bar)
            replay_trading.close()
            paper.close()
            repo.close()
            identity_repo.close()

    app = FastAPI(
        title="TMF Live Market API",
        version="0.9.0",
        description="Provider-neutral market data and isolated paper trading API.",
        lifespan=lifespan,
    )
    deps = ApiDependencies(
        config=config,
        market_repo=repo,
        strategy_repo=repo,
        validator=validator,
        identity_repo=identity_repo,
        auth_service=auth_service,
        service=service,
        paper=paper,
        replay_trading=replay_trading,
        host_monitor=HostResourceMonitor(Path(config.db_path)),
        limiter=limiter,
        paper_app=paper_app,
        research_app=research_app,
        strategy_app=strategy_app,
        runtime_app=runtime_app,
        execution_worker=execution_worker,
    )
    app.state.api_dependencies = deps
    app.state.market_service = service
    app.state.repository = repo
    app.state.auth_repository = identity_repo
    app.state.auth_service = auth_service
    app.state.paper_trading = paper
    app.state.replay_trading = replay_trading
    app.state.host_monitor = deps.host_monitor
    app.state.rate_limiter = limiter
    app.state.trading_runtime = runtime_app
    app.state.paper_auto_entry = paper_auto

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    install_authorization_middleware(app, deps)
    for router in (
        build_system_router(deps),
        build_admin_router(deps),
        build_paper_router(deps),
        build_market_router(deps),
        build_strategy_router(deps),
        build_research_router(deps),
        build_trading_runtime_router(deps),
    ):
        app.include_router(router)

    # Added last so it is the outermost application middleware and rejects
    # oversized bodies before authentication, JSON parsing, or route work.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=config.max_request_body_bytes,
    )
    return app
