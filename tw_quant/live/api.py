from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import json

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..auth import (
    AccessValidator,
    AuthService,
    CloudflareAccessValidator,
    DisabledAccessValidator,
    SQLiteAuthRepository,
)
from ..market import TradingCalendar
from ..broker import (
    SHIOAJI_TECHNICAL_CAPABILITIES,
    SQLiteBrokerTruthRepository,
    SQLiteLiveOrderRepository,
    SQLiteRecoveryLockRepository,
)
from ..execution.live_models import InstrumentSpec
from ..execution.live_policy import MarketableLimitIOCPolicy
from ..execution.shadow import ShadowExecutionService
from ..risk import LiveRiskConfig, LiveRiskService
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
    LiveShadowExecutionController,
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
from .monitoring import ExecutionHealthFileMonitor, HostResourceMonitor
from .rate_limit import RateLimitRule, SlidingWindowRateLimiter
from .request_limit import RequestBodyLimitMiddleware
from .service import LiveMarketService
from .settings import LiveSettings
from .shadow_context import (
    ConfiguredBrokerCapabilityView,
    LiveExecutionTargetCatalog,
    LiveShadowRiskContextProvider,
    StaticInstrumentSpecCatalog,
)
from .shadow_store import SQLiteShadowExecutionRepository
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
    execution_worker = ExecutionHealthFileMonitor(config.execution_health_path)
    broker_truth = SQLiteBrokerTruthRepository(config.db_path)
    recovery = SQLiteRecoveryLockRepository(config.db_path)
    live_orders = SQLiteLiveOrderRepository(config.db_path)
    shadow_store = SQLiteShadowExecutionRepository(config.db_path)
    try:
        raw_assignments = (
            json.loads(config.live_shadow_owner_targets_json)
            if config.live_shadow_enabled else {}
        )
        owner_targets = {
            str(owner): frozenset(
                str(target_id) for target_id in values
            )
            for owner, values in raw_assignments.items()
        }
    except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("LIVE_SHADOW_OWNER_TARGETS_JSON is invalid") from exc
    shadow_targets = LiveExecutionTargetCatalog(broker_truth, owner_targets)
    live_risk_config = LiveRiskConfig(
        allowed_symbols=config.live_shadow_allowed_symbols,
        allowed_contracts=config.live_shadow_allowed_contracts or frozenset({config.contract}),
        max_order_quantity=config.live_shadow_max_order_quantity,
        max_account_position_contracts=config.live_shadow_max_account_position,
        max_owner_portfolio_position_contracts=config.live_shadow_max_portfolio_position,
        max_risk_per_trade=config.live_shadow_max_risk_per_trade,
        max_daily_loss=config.live_shadow_max_daily_loss,
        max_daily_trades=config.live_shadow_max_daily_trades,
        max_pending_orders=config.live_shadow_max_pending_orders,
        max_quote_age_seconds=config.live_shadow_max_quote_age_seconds,
        max_slippage_ticks=config.live_shadow_max_slippage_ticks,
        max_spread_ticks=config.live_shadow_max_spread_ticks,
        allowed_sessions=config.live_shadow_allowed_sessions,
        expiry_guard_days=config.live_shadow_expiry_guard_days,
        max_active_live_runtimes=config.live_shadow_max_active_runtimes,
    )
    instrument_catalog = StaticInstrumentSpecCatalog({
        (symbol, contract): InstrumentSpec(
            symbol=symbol,
            contract=contract,
            tick_size=config.live_shadow_tick_size,
            multiplier=config.live_shadow_multiplier,
            expiry_date=config.live_shadow_contract_expiry,
        )
        for symbol in config.live_shadow_allowed_symbols
        for contract in (config.live_shadow_allowed_contracts or frozenset({config.contract}))
    })
    shadow_context = LiveShadowRiskContextProvider(
        truth=broker_truth,
        recovery=recovery,
        orders=live_orders,
        runtimes=repo,
        market=service,
        execution_health=execution_worker,
        shadow_store=shadow_store,
        targets=shadow_targets,
    )
    shadow_service = ShadowExecutionService(
        risk=LiveRiskService(live_risk_config),
        policy=MarketableLimitIOCPolicy(
            live_risk_config.max_slippage_ticks,
            live_risk_config.max_spread_ticks,
        ),
        quotes=service.execution_quotes,
        instruments=instrument_catalog,
        capabilities=ConfiguredBrokerCapabilityView({
            "shioaji": SHIOAJI_TECHNICAL_CAPABILITIES,
        }),
        context=shadow_context,
        store=shadow_store,
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
        repo, repo, repo, config.symbol, config.history_limit,
        live_shadow_enabled=config.live_shadow_enabled,
        execution_targets=shadow_targets,
        reservation_store=shadow_store,
    )
    paper_auto = PaperAutoExecutionController(
        repo, identity_repo, service, paper
    )
    paper_auto.recover()
    runtime_app.add_decision_listener(paper_auto.on_decision)
    live_shadow = LiveShadowExecutionController(repo, shadow_service)
    runtime_app.add_decision_listener(live_shadow.on_decision)
    service.add_bar_listener(paper_auto.before_bar)
    service.add_bar_listener(paper.on_bar)
    service.add_bar_listener(paper_auto.after_bar)
    service.add_bar_listener(runtime_app.on_bar)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        live_shadow.start()
        await execution_worker.start()
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            live_shadow.stop()
            await execution_worker.stop()
            service.remove_bar_listener(paper_auto.before_bar)
            service.remove_bar_listener(paper.on_bar)
            service.remove_bar_listener(paper_auto.after_bar)
            service.remove_bar_listener(runtime_app.on_bar)
            replay_trading.close()
            paper.close()
            shadow_store.close()
            broker_truth.close()
            recovery.close()
            live_orders.close()
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
        shadow_store=shadow_store,
        shadow_targets=shadow_targets,
        shadow_service=shadow_service,
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
    app.state.live_shadow = shadow_service

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
