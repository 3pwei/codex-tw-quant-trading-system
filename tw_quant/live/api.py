from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..auth import (
    AccessIdentity,
    AccessTokenError,
    AccessValidator,
    AccountStatus,
    AuthorizationError,
    AuthService,
    CloudflareAccessValidator,
    DisabledAccessValidator,
    Role,
    SQLiteAuthRepository,
    TradingMode,
)
from ..backtest import (
    MAX_BACKTEST_DAYS,
    run_composite_backtest,
    run_strategy_backtest,
    validate_date_range,
)
from ..market import (
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_LABELS,
    TimeframeStreamAggregator,
    TradingCalendar,
    aggregate_kbars,
    kbar_from_message,
    source_bar_limit,
    validate_timeframe,
)
from ..market_data import (
    HistoricalMarketDataProvider,
    LiveMarketDataProvider,
    build_market_data_provider,
)
from ..strategy import (
    SUPPORTED_STRATEGIES,
    analyze_strategies,
    default_composite_definition,
    generate_composite_signals,
    new_composite_id,
    strategy_catalog,
    validate_composite_definition,
    validate_strategy_parameters,
)
from .service import LiveMarketService
from .settings import LiveSettings
from .storage import (
    BarRepository,
    SQLiteBarRepository,
    StrategyPurgeError,
    StrategyReferencedError,
)


class StrategyParametersUpdate(BaseModel):
    parameters: dict[str, object]


class CompositeStrategyUpdate(BaseModel):
    definition: dict[str, object]


class CompositeStrategyPurge(BaseModel):
    strategy_ids: list[str]


class BacktestExecutionRequest(BaseModel):
    symbol: str = "TMF"
    strategy: str
    interval: str = "1m"
    start: date
    end: date
    version: int | None = None


class AdminUserCreate(BaseModel):
    email: str
    role: Role = Role.RESEARCHER
    status: AccountStatus = AccountStatus.ACTIVE
    trading_mode: TradingMode = TradingMode.DISABLED


class AdminUserUpdate(BaseModel):
    role: Role
    status: AccountStatus
    trading_mode: TradingMode


def _required_permission(method: str, path: str) -> str | None:
    """Map HTTP resources to permissions; unknown API routes fail closed."""
    if path == "/api/me":
        return None
    if path == "/api/admin/health":
        return "admin.providers.read"
    if path == "/api/admin/audit":
        return "audit.read"
    if path.startswith("/api/admin/users"):
        return "admin.users.manage"
    if path in {"/api/health", "/api/kbars", "/api/strategy-signals"}:
        return "market.read"
    if path.startswith("/api/backtest-runs"):
        if method == "DELETE":
            return "backtest_history.delete.own"
        if method == "POST":
            return "backtest.run"
        return "backtest_history.read.own"
    if path.startswith("/api/backtest") or path == "/api/composite-backtest":
        return "backtest.run"
    if path.startswith(("/api/strategies", "/api/composite-strateg")):
        return (
            "strategy.read.own"
            if method == "GET"
            else "strategy.write.own"
        )
    if path.startswith("/api/"):
        return "__deny_unknown_api__"
    return None


def _page_permission(path: str) -> str | None:
    if path.startswith("/settings"):
        return "admin.settings.read"
    if path.startswith("/admin"):
        return "admin.users.manage"
    if path.startswith("/docs") or path == "/openapi.json":
        return "admin.settings.read"
    return None


def create_app(
    settings: LiveSettings | None = None,
    feed: LiveMarketDataProvider | None = None,
    history_provider: HistoricalMarketDataProvider | None = None,
    repository: BarRepository | None = None,
    access_validator: AccessValidator | None = None,
    auth_repository: SQLiteAuthRepository | None = None,
) -> FastAPI:
    config = settings or LiveSettings.from_env()
    config.validate()
    repo = repository or SQLiteBarRepository(config.db_path)
    market_feed = feed or build_market_data_provider(config.market_data)
    if history_provider is None:
        capabilities = getattr(market_feed, "capabilities", None)
        if getattr(capabilities, "historical_bars", False):
            history_provider = market_feed
    validator = access_validator
    if validator is None:
        if config.access_mode == "cloudflare":
            validator = CloudflareAccessValidator(
                config.cloudflare_access_team_domain or "",
                config.cloudflare_access_audience or "",
            )
        else:
            validator = DisabledAccessValidator()
    identity_repo = auth_repository or SQLiteAuthRepository(config.db_path)
    identity_repo.bootstrap_admins(config.bootstrap_admin_emails)
    auth_service = AuthService(
        identity_repo, authorization_mode=config.authorization_mode
    )
    service = LiveMarketService(
        market_feed, repo, config.symbol, config.heartbeat_seconds,
        TradingCalendar(config.holidays), config.history_limit,
        history_provider=history_provider,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            repo.close()
            identity_repo.close()

    app = FastAPI(
        title="TMF Live Market API",
        version="0.7.0",
        description="Provider-neutral quote service; no order endpoints.",
        lifespan=lifespan,
    )
    app.state.market_service = service
    app.state.repository = repo
    app.state.auth_repository = identity_repo
    app.state.auth_service = auth_service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    def identity_from_headers(headers):
        token = headers.get("cf-access-jwt-assertion")
        if token:
            return validator.authenticate(token)
        subject = headers.get("x-authenticated-subject")
        if subject:
            return AccessIdentity(
                subject=subject,
                email=headers.get("x-authenticated-email"),
            )
        if config.access_mode == "disabled":
            return None
        raise AccessTokenError("missing authenticated request identity")

    def user_from_headers(headers):
        identity = identity_from_headers(headers)
        return (
            auth_service.local_development_user()
            if identity is None
            else auth_service.identify(identity)
        )

    def public_market_status(status: dict[str, object]) -> dict[str, object]:
        return {
            key: status[key]
            for key in (
                "type", "symbol", "contract", "connection_status",
                "last_tick_time", "last_heartbeat_time", "server_time",
                "latency_ms", "tick_age_ms", "history_bars_loaded",
            )
        }

    @app.middleware("http")
    async def authorize_api_requests(request: Request, call_next):
        permission = _required_permission(request.method, request.url.path)
        if not request.url.path.startswith("/api/") or request.method == "OPTIONS":
            return await call_next(request)
        try:
            user = user_from_headers(request.headers)
            if permission:
                auth_service.require_permission(user, permission)
            request.state.auth_user = user
        except AccessTokenError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=401)
        except AuthorizationError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=403)
        return await call_next(request)

    @app.get("/health/live", include_in_schema=False)
    async def liveness():
        """Process liveness for container orchestration.

        Market connectivity remains available from /api/health. A closed market
        or a broker reconnect must not make the container look dead.
        """
        return {"status": "ok"}

    @app.get("/internal/auth/cloudflare", include_in_schema=False)
    def cloudflare_origin_auth(request: Request):
        try:
            identity = validator.authenticate(
                request.headers.get("cf-access-jwt-assertion")
            )
        except AccessTokenError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        try:
            user = auth_service.identify(identity)
            page_permission = _page_permission(
                request.headers.get("x-original-uri", "/")
            )
            if page_permission:
                auth_service.require_permission(user, page_permission)
        except AuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        headers = {"X-Authenticated-Subject": identity.subject}
        if identity.email:
            headers["X-Authenticated-Email"] = identity.email
        if user.registered:
            headers["X-Authenticated-User-ID"] = user.user_id
            headers["X-Authenticated-Role"] = user.role.value
        return Response(status_code=204, headers=headers)

    @app.get("/api/me")
    def current_user(request: Request):
        user = request.state.auth_user
        return user.to_message(auth_service.enforced)

    @app.get("/api/health")
    async def health():
        return public_market_status(service.status_message())

    @app.get("/api/admin/health")
    async def admin_health():
        return service.status_message()

    @app.get("/api/admin/users")
    def admin_users():
        return {"users": [user.to_message(auth_service.enforced) for user in identity_repo.users()]}

    @app.post("/api/admin/users", status_code=201)
    def create_admin_user(update: AdminUserCreate, request: Request):
        try:
            user = identity_repo.create_user(
                update.email,
                role=update.role,
                status=update.status,
                trading_mode=update.trading_mode,
                actor_user_id=request.state.auth_user.user_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return user.to_message(auth_service.enforced)

    @app.put("/api/admin/users/{user_id}")
    def update_admin_user(
        user_id: str, update: AdminUserUpdate, request: Request
    ):
        actor = request.state.auth_user
        if user_id == actor.user_id and (
            update.status is not AccountStatus.ACTIVE or update.role is not Role.ADMIN
        ):
            raise HTTPException(
                status_code=409,
                detail="cannot disable or demote your own admin account",
            )
        try:
            user = identity_repo.update_user(
                user_id,
                role=update.role,
                status=update.status,
                trading_mode=update.trading_mode,
                actor_user_id=actor.user_id,
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return user.to_message(auth_service.enforced)

    @app.get("/api/admin/audit")
    def admin_audit(limit: int = Query(200, ge=1, le=1000)):
        return {"events": identity_repo.audit_events(limit)}

    @app.get("/api/kbars")
    async def kbars(
        symbol: str = "TMF",
        interval: str = "1m",
        limit: int = Query(500, ge=1, le=5000),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        try:
            selected_interval = validate_timeframe(interval)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        source_limit = source_bar_limit(
            selected_interval, limit, config.history_limit
        )
        return [
            bar.to_message(service.connection_status, selected_interval)
            for bar in aggregate_kbars(
                repo.latest(config.symbol, source_limit), selected_interval, limit
            )
        ]

    @app.get("/api/strategy-signals")
    async def strategy_signals(
        symbol: str = "TMF",
        strategies: str = "orb,bnf",
        interval: str = "1m",
        limit: int = Query(500, ge=20, le=5000),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        selected = [
            value.strip().lower()
            for value in strategies.split(",")
            if value.strip()
        ]
        if not selected:
            return {"strategies": []}
        unsupported = sorted(set(selected) - set(SUPPORTED_STRATEGIES))
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported strategies: {', '.join(unsupported)}",
            )
        try:
            selected_interval = validate_timeframe(interval)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        source_limit = source_bar_limit(
            selected_interval, limit, config.history_limit
        )
        return analyze_strategies(
            aggregate_kbars(
                repo.latest(config.symbol, source_limit), selected_interval, limit
            ),
            selected,
            parameters=repo.strategy_parameters(),
            interval=selected_interval,
        )

    @app.get("/api/strategies")
    def strategies_catalog():
        return {"strategies": strategy_catalog(repo.strategy_parameters())}

    @app.put("/api/strategies/{strategy}")
    def update_strategy_parameters(
        strategy: str, update: StrategyParametersUpdate
    ):
        key = strategy.lower()
        if key not in SUPPORTED_STRATEGIES:
            raise HTTPException(status_code=404, detail="unsupported strategy")
        try:
            parameters = validate_strategy_parameters(key, update.parameters)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        repo.save_strategy_parameters(key, parameters)
        return next(
            item
            for item in strategy_catalog(repo.strategy_parameters())
            if item["key"] == key
        )

    @app.get("/api/composite-strategies")
    def composite_strategies():
        return {
            "template": default_composite_definition(),
            "strategies": repo.composite_strategies(),
            "archived_strategies": repo.archived_composite_strategies(),
        }

    @app.get("/api/composite-strategies/{strategy_id}/versions")
    def composite_strategy_versions(strategy_id: str):
        versions = repo.composite_strategy_versions(strategy_id)
        if not versions:
            raise HTTPException(status_code=404, detail="找不到組合策略")
        return {
            "id": strategy_id,
            "archived": repo.composite_strategy_archived(strategy_id),
            "versions": versions,
        }

    @app.get("/api/composite-strategies/{strategy_id}")
    def composite_strategy(strategy_id: str, version: int | None = None):
        item = repo.composite_strategy(strategy_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        return item

    @app.get("/api/composite-strategy-signals/{strategy_id}")
    def composite_strategy_signals(
        strategy_id: str,
        version: int | None = None,
        symbol: str = "TMF",
        limit: int = Query(5000, ge=20, le=5000),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        item = repo.composite_strategy(strategy_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        signals, trace = generate_composite_signals(
            repo.latest(config.symbol, limit), item["definition"]
        )
        return {
            "id": item["id"],
            "version": item["version"],
            "name": item["name"],
            "signals": signals,
            "trace": trace,
        }

    @app.post("/api/composite-strategies", status_code=201)
    def create_composite_strategy(update: CompositeStrategyUpdate):
        try:
            definition = validate_composite_definition(
                update.definition, repo.strategy_parameters()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return repo.save_composite_strategy(new_composite_id(), definition)

    @app.put("/api/composite-strategies/{strategy_id}")
    def update_composite_strategy(
        strategy_id: str, update: CompositeStrategyUpdate
    ):
        if repo.composite_strategy(strategy_id) is None:
            raise HTTPException(status_code=404, detail="找不到組合策略")
        if repo.composite_strategy_archived(strategy_id):
            raise HTTPException(status_code=410, detail="組合策略已封存")
        try:
            definition = validate_composite_definition(
                update.definition, repo.strategy_parameters()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return repo.save_composite_strategy(strategy_id, definition)

    @app.delete("/api/composite-strategies/{strategy_id}")
    def archive_composite_strategy(strategy_id: str):
        try:
            return repo.archive_composite_strategy(strategy_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/composite-strategies/purge")
    def purge_composite_strategies(request: CompositeStrategyPurge):
        if len(request.strategy_ids) > 100:
            raise HTTPException(status_code=422, detail="單次最多永久刪除 100 個策略")
        try:
            return repo.purge_archived_composite_strategies(request.strategy_ids)
        except StrategyReferencedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StrategyPurgeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/backtest/options")
    def backtest_options(symbol: str = "TMF"):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        first, last = repo.date_bounds(config.symbol)
        catalog = analyze_strategies(
            [], SUPPORTED_STRATEGIES, parameters=repo.strategy_parameters()
        )["strategies"]
        return {
            "symbol": config.symbol,
            "available_start": first.isoformat() if first else None,
            "available_end": last.isoformat() if last else None,
            "max_days": MAX_BACKTEST_DAYS,
            "intervals": [
                {"key": key, "name": TIMEFRAME_LABELS[key]}
                for key in SUPPORTED_TIMEFRAMES
            ],
            "strategies": [
                {"key": item["key"], "name": item["name"]} for item in catalog
            ] + [
                {
                    "key": f"composite:{item['id']}",
                    "name": f"{item['name']} · v{item['version']}",
                    "kind": "composite",
                }
                for item in repo.composite_strategies()
            ],
        }

    @app.get("/api/backtest")
    def backtest(
        symbol: str = "TMF",
        strategy: str = "orb",
        interval: str = "1m",
        start: date = Query(...),
        end: date = Query(...),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        if strategy.lower() not in SUPPORTED_STRATEGIES:
            raise HTTPException(status_code=400, detail="unsupported strategy")
        try:
            selected_interval = validate_timeframe(interval)
            validate_date_range(start, end)
            bars = aggregate_kbars(
                repo.between_trading_dates(config.symbol, start, end),
                selected_interval,
            )
            return run_strategy_backtest(
                bars,
                strategy.lower(),
                start,
                end,
                interval=selected_interval,
                parameters=repo.strategy_parameters().get(strategy.lower()),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/composite-backtest")
    def composite_backtest(
        strategy_id: str,
        version: int | None = None,
        symbol: str = "TMF",
        start: date = Query(...),
        end: date = Query(...),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        item = repo.composite_strategy(strategy_id, version)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        try:
            validate_date_range(start, end)
            return run_composite_backtest(
                repo.between_trading_dates(config.symbol, start, end),
                item["definition"],
                str(item["id"]),
                int(item["version"]),
                start,
                end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/backtest-runs", status_code=201)
    def create_backtest_run(request: BacktestExecutionRequest):
        if request.strategy.startswith("composite:"):
            strategy_id = request.strategy.removeprefix("composite:")
            item = repo.composite_strategy(strategy_id, request.version)
            if item is None:
                raise HTTPException(status_code=404, detail="找不到組合策略版本")
            result = composite_backtest(
                strategy_id=strategy_id,
                version=int(item["version"]),
                symbol=request.symbol,
                start=request.start,
                end=request.end,
            )
            saved = repo.save_backtest_run(
                result, "composite", strategy_id, int(item["version"]),
                item["definition"],
            )
        else:
            key = request.strategy.lower()
            result = backtest(
                symbol=request.symbol,
                strategy=key,
                interval=request.interval,
                start=request.start,
                end=request.end,
            )
            snapshot = validate_strategy_parameters(
                key, repo.strategy_parameters().get(key)
            )
            saved = repo.save_backtest_run(
                result, "atomic", key, None, snapshot,
            )
        result["history_run_id"] = saved["run_id"]
        result["history_created_at"] = saved["created_at"]
        return result

    @app.get("/api/backtest-runs")
    def backtest_runs(
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        strategy_key: str | None = None,
    ):
        return {
            "runs": repo.backtest_runs(limit, offset, strategy_key),
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/backtest-runs/{run_id}")
    def backtest_run(run_id: str):
        item = repo.backtest_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到回測紀錄")
        return item

    @app.delete("/api/backtest-runs/{run_id}")
    def delete_backtest_run(run_id: str):
        item = repo.delete_backtest_run(run_id)
        if item is None:
            raise HTTPException(status_code=404, detail="找不到回測紀錄")
        return {
            "deleted_run_id": run_id,
            "strategy_key": item["strategy_key"],
            "strategy_version": item["strategy_version"],
            "released_strategy_reference": item["released_strategy_reference"],
        }

    @app.websocket("/ws/market/{symbol}")
    async def market_socket(
        websocket: WebSocket, symbol: str, interval: str = "1m"
    ):
        try:
            socket_user = user_from_headers(websocket.headers)
            auth_service.require_permission(socket_user, "market.read")
        except (AccessTokenError, AuthorizationError) as exc:
            await websocket.close(code=1008, reason=str(exc))
            return
        if symbol.upper() != config.symbol:
            await websocket.close(code=1008, reason="unsupported symbol")
            return
        try:
            selected_interval = validate_timeframe(interval)
        except ValueError:
            await websocket.close(code=1008, reason="unsupported interval")
            return
        await websocket.accept()
        queue = service.hub.subscribe()
        transformer = TimeframeStreamAggregator(
            selected_interval,
            repo.latest(config.symbol, config.history_limit),
        )
        await websocket.send_json(public_market_status(service.status_message()))
        try:
            while True:
                message = await queue.get()
                if message.get("type") != "kbar":
                    await websocket.send_json(public_market_status(message))
                    continue
                for bar in transformer.push(kbar_from_message(message)):
                    await websocket.send_json(
                        bar.to_message(service.connection_status, selected_interval)
                    )
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            service.hub.unsubscribe(queue)

    return app


app = create_app()
