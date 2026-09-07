from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from ..auth import (
    AccessIdentity,
    AccessRequestStatus,
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
    run_historical_events,
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
from ..paper import PaperOrderCommand, PaperTradingService, SQLitePaperRepository
from ..replay import ReplaySessionNotFound, ReplayTradingSessionRegistry
from ..strategy import (
    SUPPORTED_STRATEGIES,
    analyze_strategies,
    default_composite_definition,
    generate_composite_signals,
    new_composite_id,
    strategy_catalog,
    validate_composite_dependencies,
    validate_composite_definition,
    validate_strategy_parameters,
)
from .monitoring import HostResourceMonitor
from .rate_limit import RateLimitDecision, RateLimitRule, SlidingWindowRateLimiter
from .request_limit import RequestBodyLimitMiddleware
from .service import LiveMarketService
from .settings import LiveSettings
from .storage import (
    DEFAULT_OWNER_ID,
    BarRepository,
    SQLiteBarRepository,
    StrategyNameConflictError,
    StrategyPurgeError,
    StrategyReferencedError,
)


ShortIdentifier = Annotated[str, Field(min_length=1, max_length=80)]
EmailAddress = Annotated[str, Field(min_length=3, max_length=254)]


class StrategyParametersUpdate(BaseModel):
    parameters: dict[str, object] = Field(max_length=50)


class CompositeStrategyUpdate(BaseModel):
    definition: dict[str, object] = Field(max_length=20)


class CompositeStrategyPurge(BaseModel):
    strategy_ids: list[ShortIdentifier] = Field(min_length=1, max_length=100)


class BacktestExecutionRequest(BaseModel):
    symbol: Annotated[str, Field(min_length=1, max_length=32)] = "TMF"
    strategy: ShortIdentifier
    interval: Annotated[str, Field(min_length=1, max_length=16)] = "1m"
    start: date
    end: date
    version: int | None = None


class ReplayPrepareRequest(BaseModel):
    symbol: Annotated[str, Field(min_length=1, max_length=32)] = "TMF"
    trading_date: date
    session: Literal["day", "night"] = "day"
    interval: Annotated[str, Field(min_length=1, max_length=16)] = "1m"
    strategies: list[ShortIdentifier] = Field(min_length=1, max_length=3)


class ReplayCursorUpdate(BaseModel):
    cursor: int


class AdminUserCreate(BaseModel):
    email: EmailAddress
    role: Role = Role.RESEARCHER
    status: AccountStatus = AccountStatus.ACTIVE
    trading_mode: TradingMode = TradingMode.DISABLED


class AdminUserUpdate(BaseModel):
    role: Role
    status: AccountStatus
    trading_mode: TradingMode


class PaperOrderCreate(BaseModel):
    strategy_id: ShortIdentifier = "manual"
    strategy_version: int = 1
    side: Literal["buy", "sell"]
    quantity: int = 1
    stop_loss_price: float | None = None
    reduce_only: bool = False


class PaperControlRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


def system_status(
    market: dict[str, object], paper: dict[str, object],
    host: dict[str, object] | None = None,
) -> str:
    market_status = str(market["service_status"])
    if market_status in {"provider_disconnected", "market_stale"}:
        return market_status
    if (
        int(paper.get("active_kill_switches", 0)) > 0
        or int(paper.get("inconsistent_owners", 0)) > 0
    ):
        return "trading_halted"
    if market_status != "healthy" or paper.get("status") != "healthy":
        return "degraded"
    if host and any(
        isinstance(host.get(key), (int, float)) and float(host[key]) >= 90
        for key in ("cpu_percent", "memory_percent", "disk_percent")
    ):
        return "degraded"
    return "healthy"


def _required_permission(method: str, path: str) -> str | None:
    """Map HTTP resources to permissions; unknown API routes fail closed."""
    if path in {"/api/me", "/api/access-requests"}:
        return None
    if path == "/api/admin/health":
        return "admin.providers.read"
    if path == "/api/admin/audit":
        return "audit.read"
    if path.startswith(("/api/admin/users", "/api/admin/access-requests")):
        return "admin.users.manage"
    if path in {"/api/health", "/api/kbars", "/api/strategy-signals"}:
        return "market.read"
    if path.startswith("/api/replay"):
        return "backtest.run"
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
    if path.startswith("/api/paper/orders"):
        return "orders.paper" if method == "POST" else "positions.read.own"
    if path.startswith("/api/paper/kill-switch"):
        return "orders.paper"
    if path.startswith("/api/paper"):
        return "positions.read.own"
    if path.startswith("/api/"):
        return "__deny_unknown_api__"
    return None


def _page_permission(path: str) -> str | None:
    if path.startswith("/trade"):
        return "market.read"
    if path.startswith("/paper"):
        return "positions.read.own"
    if path.startswith("/settings"):
        return "admin.settings.read"
    if path.startswith("/admin"):
        return "admin.users.manage"
    if path.startswith("/docs") or path == "/openapi.json":
        return "admin.settings.read"
    return None


def _rate_limit_scope(method: str, path: str) -> str | None:
    if method == "POST" and path == "/api/access-requests":
        return "access_requests"
    if (
        (
            method == "GET"
            and path in {"/api/backtest", "/api/composite-backtest"}
        )
        or (method == "POST" and path == "/api/backtest-runs")
    ):
        return "backtests"
    if method == "POST" and path == "/api/replay/prepare":
        return "replay_prepares"
    if method == "POST" and (
        path == "/api/paper/orders"
        or (
            path.startswith("/api/replay/sessions/")
            and path.endswith("/orders")
        )
    ):
        return "orders"
    return None


def _rate_limit_headers(decision: RateLimitDecision) -> dict[str, str]:
    headers = {
        "X-RateLimit-Limit": str(decision.limit),
        "X-RateLimit-Remaining": str(decision.remaining),
        "X-RateLimit-Reset": str(decision.reset_after),
    }
    if not decision.allowed:
        headers["Retry-After"] = str(decision.retry_after)
    return headers


def _authorization_denied_response(
    original_uri: str, error: AuthorizationError
) -> Response:
    detail = str(error)
    if original_uri.startswith(("/api/", "/ws/")):
        return JSONResponse(
            {"detail": detail},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
    request_action = ""
    if "not registered" in detail:
        title = "帳號尚未開通"
        message = "Email 已完成驗證，但尚未列入平台使用者名單。"
        request_action = """
    <button id="request-access" type="button">申請開通</button>
    <p id="request-status" role="status"></p>
    <script>
      const button = document.getElementById("request-access");
      const status = document.getElementById("request-status");
      button.addEventListener("click", async () => {
        button.disabled = true;
        status.textContent = "正在送出申請…";
        try {
          const response = await fetch("/api/access-requests", {
            method: "POST",
            headers: {"Content-Type": "application/json"}
          });
          const body = await response.json();
          if (!response.ok) throw new Error(body.detail || "申請送出失敗");
          button.hidden = true;
          status.className = "success";
          status.textContent = "申請已送出，請等待平台管理員審核。";
        } catch (error) {
          button.disabled = false;
          status.className = "error";
          status.textContent = error instanceof Error
            ? error.message : "申請送出失敗，請稍後再試。";
        }
      });
    </script>
"""
    elif "suspended" in detail or "revoked" in detail:
        title = "帳號目前無法使用"
        message = "此帳號已被暫停或撤銷，請聯絡平台管理員。"
    else:
        title = "你沒有此頁面的權限"
        message = "帳號已登入，但目前角色不允許使用這項管理功能。"
    return HTMLResponse(
        f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="robots" content="noindex,nofollow">
  <title>{title}｜Wade Quant Lab</title>
  <style>
    :root{{color-scheme:dark;font-family:Inter,"Noto Sans TC",sans-serif}}
    *{{box-sizing:border-box}}
    body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;
      color:#edf7f3;background:#07110f}}
    main{{width:min(520px,100%);padding:36px;border:1px solid rgba(157,197,184,.25);
      background:#0c1815;box-shadow:0 24px 80px rgba(0,0,0,.35)}}
    small{{color:#42d6a4;font:700 10px ui-monospace,monospace;letter-spacing:.18em}}
    h1{{margin:14px 0 0;font-size:28px}}p{{margin:16px 0;color:#9bb0aa;line-height:1.7}}
    button,a{{display:inline-block;margin-top:12px;padding:11px 15px;border:0;color:#07110f;
      background:#42d6a4;text-decoration:none;font-weight:800;cursor:pointer}}
    button:disabled{{opacity:.55;cursor:wait}}a{{margin-left:8px;background:transparent;
      color:#9bb0aa;border:1px solid rgba(157,197,184,.3)}}
    #request-status{{min-height:20px;margin:14px 0 0;font-size:13px}}
    #request-status.success{{color:#42d6a4}}#request-status.error{{color:#ff6b72}}
  </style>
</head>
<body>
  <main>
    <small>ACCESS CONTROL</small>
    <h1>{title}</h1>
    <p>{message}</p>
    {request_action}
    <a href="/cdn-cgi/access/logout">登出並改用其他 Email</a>
  </main>
</body>
</html>""",
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


def create_app(
    settings: LiveSettings | None = None,
    feed: LiveMarketDataProvider | None = None,
    history_provider: HistoricalMarketDataProvider | None = None,
    repository: BarRepository | None = None,
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
    if config.bootstrap_admin_emails:
        bootstrap_owner = identity_repo.user_by_email(
            config.bootstrap_admin_emails[0]
        )
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
    host_monitor = HostResourceMonitor(Path(config.db_path))
    limiter = rate_limiter or SlidingWindowRateLimiter(
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
    service.add_bar_listener(paper.on_bar)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            service.remove_bar_listener(paper.on_bar)
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
    app.state.market_service = service
    app.state.repository = repo
    app.state.auth_repository = identity_repo
    app.state.auth_service = auth_service
    app.state.paper_trading = paper
    app.state.replay_trading = replay_trading
    app.state.host_monitor = host_monitor
    app.state.rate_limiter = limiter
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
                "type", "service_status", "symbol", "contract", "connection_status",
                "last_tick_time", "last_bar_time", "last_heartbeat_time",
                "server_time", "latency_ms", "market_latency_seconds",
                "tick_age_ms", "tick_age_seconds", "bar_age_seconds",
                "stale_after_seconds", "trading_block_reason",
                "history_bars_loaded",
            )
        }

    def request_owner_id(request: Request) -> str:
        user = request.state.auth_user
        return user.user_id if user.registered else DEFAULT_OWNER_ID

    def validated_composite_for_save(
        raw: dict[str, object], owner_id: str, strategy_id: str
    ) -> dict[str, object]:
        def resolve_child(
            child_id: str, child_version: int
        ) -> dict[str, object] | None:
            child = repo.composite_strategy(
                child_id, child_version, owner_user_id=owner_id
            )
            if child is None:
                return None
            if repo.composite_strategy_archived(child_id, owner_id):
                raise ValueError(
                    f"封存策略不可加入新的組合：{child['name']} v{child_version}"
                )
            return child

        definition = validate_composite_definition(
            raw,
            repo.strategy_parameters(owner_id),
            composite_resolver=resolve_child,
        )
        validate_composite_dependencies(definition, strategy_id)
        return definition

    @app.middleware("http")
    async def authorize_api_requests(request: Request, call_next):
        permission = _required_permission(request.method, request.url.path)
        if not request.url.path.startswith("/api/") or request.method == "OPTIONS":
            return await call_next(request)
        try:
            if (
                request.url.path == "/api/access-requests"
                and request.method == "POST"
            ):
                identity = identity_from_headers(request.headers)
                if identity is None:
                    raise AccessTokenError(
                        "missing authenticated request identity"
                    )
                request.state.access_identity = identity
                actor = identity.subject
            else:
                user = user_from_headers(request.headers)
                if permission:
                    auth_service.require_permission(user, permission)
                request.state.auth_user = user
                actor = user.user_id
        except AccessTokenError as exc:
            return JSONResponse(
                {"detail": str(exc)},
                status_code=401,
                headers={"Cache-Control": "no-store"},
            )
        except AuthorizationError as exc:
            return JSONResponse(
                {"detail": str(exc)},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
        scope = _rate_limit_scope(request.method, request.url.path)
        if scope is None:
            return await call_next(request)
        decision = limiter.check(scope, actor)
        headers = _rate_limit_headers(decision)
        if not decision.allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded", "scope": scope},
                status_code=429,
                headers={**headers, "Cache-Control": "no-store"},
            )
        response = await call_next(request)
        response.headers.update(headers)
        return response

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
        original_uri = request.headers.get("x-original-uri", "/")
        original_path = original_uri.split("?", 1)[0]
        headers = {"X-Authenticated-Subject": identity.subject}
        if identity.email:
            headers["X-Authenticated-Email"] = identity.email
        if original_path == "/api/access-requests":
            return Response(status_code=204, headers=headers)
        try:
            user = auth_service.identify(identity)
            page_permission = _page_permission(original_path)
            if page_permission:
                auth_service.require_permission(user, page_permission)
        except AuthorizationError as exc:
            return _authorization_denied_response(original_uri, exc)
        if user.registered:
            headers["X-Authenticated-User-ID"] = user.user_id
            headers["X-Authenticated-Role"] = user.role.value
        return Response(status_code=204, headers=headers)

    @app.post("/api/access-requests", status_code=201)
    def submit_access_request(request: Request):
        try:
            access_request = identity_repo.submit_access_request(
                request.state.access_identity
            )
        except ValueError as exc:
            status_code = 409 if "already registered" in str(exc) else 422
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return {"request": access_request.to_message()}

    @app.get("/api/me")
    def current_user(request: Request):
        user = request.state.auth_user
        return user.to_message(auth_service.enforced)

    @app.get("/api/health")
    async def health():
        return public_market_status(service.status_message())

    @app.get("/api/admin/health")
    async def admin_health():
        market = service.status_message()
        paper_health = paper.health()
        host = host_monitor.snapshot()
        return {
            **market,
            "system_status": system_status(market, paper_health, host),
            "paper_trading": paper_health,
            "host": host,
            "rate_limiting": limiter.stats(),
            "request_limits": {
                "max_body_bytes": config.max_request_body_bytes,
            },
        }

    @app.get("/api/admin/access-requests")
    def admin_access_requests(
        status: AccessRequestStatus | None = AccessRequestStatus.PENDING,
    ):
        return {
            "requests": [
                access_request.to_message()
                for access_request in identity_repo.access_requests(status)
            ]
        }

    @app.post("/api/admin/access-requests/{request_id}/approve")
    def approve_access_request(request_id: str, request: Request):
        try:
            access_request, user = identity_repo.approve_access_request(
                request_id, actor_user_id=request.state.auth_user.user_id
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return {
            "request": access_request.to_message(),
            "user": user.to_message(auth_service.enforced),
        }

    @app.post("/api/admin/access-requests/{request_id}/reject")
    def reject_access_request(request_id: str, request: Request):
        try:
            access_request = identity_repo.reject_access_request(
                request_id, actor_user_id=request.state.auth_user.user_id
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return {"request": access_request.to_message()}

    @app.get("/api/admin/users")
    def admin_users():
        return {
            "users": [
                user.to_message(auth_service.enforced)
                for user in identity_repo.users()
            ]
        }

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

    @app.get("/api/paper/account")
    def paper_account(request: Request):
        owner_id = request.state.auth_user.user_id
        return {
            "mode": "paper",
            "account": paper.account(owner_id),
            "positions": paper.positions(owner_id),
        }

    @app.get("/api/paper/orders")
    def paper_orders(request: Request):
        return {"orders": paper.orders(request.state.auth_user.user_id)}

    @app.get("/api/paper/fills")
    def paper_fills(
        request: Request, limit: int = Query(200, ge=1, le=1000)
    ):
        return {"fills": paper.fills(request.state.auth_user.user_id)[:limit]}

    @app.get("/api/paper/events")
    def paper_events(
        request: Request, limit: int = Query(200, ge=1, le=1000)
    ):
        return {
            "events": paper.repository.events(
                request.state.auth_user.user_id, limit
            )
        }

    @app.post("/api/paper/orders", status_code=201)
    def create_paper_order(
        payload: PaperOrderCreate,
        request: Request,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=1, max_length=128
        ),
    ):
        market = service.status_message()
        block_reason = market.get("trading_block_reason")
        if not payload.reduce_only and block_reason:
            paper.record_market_block()
            detail = (
                "market data provider is disconnected; new positions are blocked"
                if block_reason == "provider_disconnected"
                else "market data is stale; new positions are blocked"
            )
            raise HTTPException(status_code=503, detail=detail)
        latest = repo.latest(config.symbol, 1)
        if not latest:
            paper.record_market_block()
            raise HTTPException(
                status_code=503, detail="market price is not available"
            )
        quote_age = datetime.now(latest[0].received_time.tzinfo) - latest[0].received_time
        if quote_age > timedelta(seconds=config.stale_after_seconds):
            paper.record_market_block()
            raise HTTPException(
                status_code=503, detail="market price is stale"
            )
        try:
            order, created = paper.submit(
                request.state.auth_user,
                PaperOrderCommand(
                    strategy_id=payload.strategy_id.strip(),
                    strategy_version=payload.strategy_version,
                    side=payload.side,
                    quantity=payload.quantity,
                    stop_loss_price=payload.stop_loss_price,
                    reduce_only=payload.reduce_only,
                ),
                idempotency_key=idempotency_key,
                market_bar=latest[0],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"created": created, "order": order}

    @app.post("/api/paper/kill-switch")
    def activate_paper_kill_switch(
        payload: PaperControlRequest, request: Request
    ):
        try:
            paper.activate_kill_switch(
                request.state.auth_user.user_id, payload.reason.strip()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return paper.account(request.state.auth_user.user_id)

    @app.post("/api/paper/kill-switch/reset")
    def reset_paper_kill_switch(
        payload: PaperControlRequest, request: Request
    ):
        try:
            paper.reset_kill_switch(
                request.state.auth_user.user_id, payload.reason.strip()
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return paper.account(request.state.auth_user.user_id)

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
        request: Request,
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
            parameters=repo.strategy_parameters(request_owner_id(request)),
            interval=selected_interval,
        )

    @app.get("/api/strategies")
    def strategies_catalog(request: Request):
        return {
            "strategies": strategy_catalog(
                repo.strategy_parameters(request_owner_id(request))
            )
        }

    @app.put("/api/strategies/{strategy}")
    def update_strategy_parameters(
        strategy: str, update: StrategyParametersUpdate, request: Request
    ):
        key = strategy.lower()
        if key not in SUPPORTED_STRATEGIES:
            raise HTTPException(status_code=404, detail="unsupported strategy")
        try:
            parameters = validate_strategy_parameters(key, update.parameters)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        owner_id = request_owner_id(request)
        repo.save_strategy_parameters(key, parameters, owner_id)
        return next(
            item
            for item in strategy_catalog(repo.strategy_parameters(owner_id))
            if item["key"] == key
        )

    @app.get("/api/composite-strategies")
    def composite_strategies(request: Request):
        owner_id = request_owner_id(request)
        active = repo.composite_strategies(owner_id)
        return {
            "template": default_composite_definition(),
            "strategies": active,
            "archived_strategies": repo.archived_composite_strategies(owner_id),
            "reference_strategies": [
                {
                    "id": item["id"],
                    "name": item["name"],
                    "versions": [
                        {"version": version["version"], "name": version["name"]}
                        for version in repo.composite_strategy_versions(
                            str(item["id"]), owner_id
                        )
                    ],
                }
                for item in active
            ],
        }

    @app.get("/api/composite-strategies/{strategy_id}/versions")
    def composite_strategy_versions(strategy_id: str, request: Request):
        owner_id = request_owner_id(request)
        versions = repo.composite_strategy_versions(strategy_id, owner_id)
        if not versions:
            raise HTTPException(status_code=404, detail="找不到組合策略")
        return {
            "id": strategy_id,
            "archived": repo.composite_strategy_archived(strategy_id, owner_id),
            "versions": versions,
        }

    @app.get("/api/composite-strategies/{strategy_id}")
    def composite_strategy(
        strategy_id: str, request: Request, version: int | None = None
    ):
        item = repo.composite_strategy(
            strategy_id, version, request_owner_id(request)
        )
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        return item

    @app.get("/api/composite-strategy-signals/{strategy_id}")
    def composite_strategy_signals(
        strategy_id: str,
        request: Request,
        version: int | None = None,
        symbol: str = "TMF",
        limit: int = Query(5000, ge=20, le=5000),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        item = repo.composite_strategy(
            strategy_id, version, request_owner_id(request)
        )
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
    def create_composite_strategy(
        update: CompositeStrategyUpdate, request: Request
    ):
        owner_id = request_owner_id(request)
        strategy_id = new_composite_id()
        try:
            definition = validated_composite_for_save(
                update.definition, owner_id, strategy_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            return repo.save_composite_strategy(
                strategy_id, definition, owner_id
            )
        except StrategyNameConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.put("/api/composite-strategies/{strategy_id}")
    def update_composite_strategy(
        strategy_id: str, update: CompositeStrategyUpdate, request: Request
    ):
        owner_id = request_owner_id(request)
        current = repo.composite_strategy(strategy_id, owner_user_id=owner_id)
        if current is None:
            raise HTTPException(status_code=404, detail="找不到組合策略")
        if repo.composite_strategy_archived(strategy_id, owner_id):
            raise HTTPException(status_code=410, detail="組合策略已封存")
        candidate_name = str(update.definition.get("name", "")).strip()
        target_id = (
            new_composite_id()
            if candidate_name != current["name"]
            else strategy_id
        )
        try:
            definition = validated_composite_for_save(
                update.definition, owner_id, target_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        try:
            saved = repo.save_composite_strategy(target_id, definition, owner_id)
        except StrategyNameConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if target_id != strategy_id:
            saved["created_from_strategy_id"] = strategy_id
        return saved

    @app.delete("/api/composite-strategies/{strategy_id}")
    def archive_composite_strategy(strategy_id: str, request: Request):
        try:
            return repo.archive_composite_strategy(
                strategy_id, request_owner_id(request)
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/composite-strategies/purge")
    def purge_composite_strategies(
        purge: CompositeStrategyPurge, request: Request
    ):
        if len(purge.strategy_ids) > 100:
            raise HTTPException(status_code=422, detail="單次最多永久刪除 100 個策略")
        try:
            return repo.purge_archived_composite_strategies(
                purge.strategy_ids, request_owner_id(request)
            )
        except StrategyReferencedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except StrategyPurgeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/backtest/options")
    def backtest_options(request: Request, symbol: str = "TMF"):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        first, last = repo.date_bounds(config.symbol)
        catalog = analyze_strategies(
            [], SUPPORTED_STRATEGIES,
            parameters=repo.strategy_parameters(request_owner_id(request)),
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
                for item in repo.composite_strategies(request_owner_id(request))
            ],
        }

    @app.get("/api/replay/options")
    def replay_options(request: Request, symbol: str = "TMF"):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        first, last = repo.date_bounds(config.symbol)
        owner_id = request_owner_id(request)
        catalog = strategy_catalog(repo.strategy_parameters(owner_id))
        return {
            "symbol": config.symbol,
            "available_start": first.isoformat() if first else None,
            "available_end": last.isoformat() if last else None,
            "available_dates": repo.replay_availability(config.symbol),
            "intervals": [
                {"key": key, "name": TIMEFRAME_LABELS[key]}
                for key in SUPPORTED_TIMEFRAMES
                if key not in {"1d", "1w"}
            ],
            "strategies": [
                {
                    "key": item["key"],
                    "name": item["name"],
                    "kind": "atomic",
                    "color": item["color"],
                }
                for item in catalog
            ] + [
                {
                    "key": f"composite:{item['id']}",
                    "name": f"{item['name']} · v{item['version']}",
                    "kind": "composite",
                    "color": "#a78bfa",
                }
                for item in repo.composite_strategies(owner_id)
            ],
            "max_strategies": 3,
            "sessions": [
                {"key": "day", "name": "日盤"},
                {"key": "night", "name": "夜盤"},
            ],
        }

    @app.post("/api/replay/prepare")
    def prepare_replay(payload: ReplayPrepareRequest, request: Request):
        if payload.symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        selected = list(dict.fromkeys(
            value.strip().lower() for value in payload.strategies if value.strip()
        ))
        if not selected:
            raise HTTPException(status_code=422, detail="至少選擇一個策略")
        if len(selected) > 3:
            raise HTTPException(status_code=422, detail="回放最多同時顯示 3 個策略")
        try:
            interval = validate_timeframe(payload.interval)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if interval in {"1d", "1w"}:
            raise HTTPException(status_code=400, detail="回放僅支援 1 分鐘至 1 小時 K")

        owner_id = request_owner_id(request)
        source_bars = [
            bar for bar in repo.between_trading_dates(
                config.symbol, payload.trading_date, payload.trading_date
            )
            if bar.session == payload.session
        ]
        if not source_bars:
            raise HTTPException(status_code=404, detail="所選交易日與時段沒有歷史 K 棒")
        display_bars = aggregate_kbars(source_bars, interval)
        atomic = [key for key in selected if not key.startswith("composite:")]
        unsupported = sorted(set(atomic) - set(SUPPORTED_STRATEGIES))
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported strategies: {', '.join(unsupported)}",
            )
        results = analyze_strategies(
            display_bars,
            atomic,
            parameters=repo.strategy_parameters(owner_id),
            interval=interval,
        )["strategies"]
        by_key = {str(item["key"]): item for item in results}
        for key in selected:
            if not key.startswith("composite:"):
                continue
            strategy_id = key.removeprefix("composite:")
            item = repo.composite_strategy(strategy_id, owner_user_id=owner_id)
            if item is None or repo.composite_strategy_archived(strategy_id, owner_id):
                raise HTTPException(status_code=404, detail="找不到可用的組合策略")
            signals, _trace = generate_composite_signals(
                source_bars, item["definition"]
            )
            by_key[key] = {
                "key": key,
                "name": f"{item['name']} · v{item['version']}",
                "color": "#a78bfa",
                "parameters": {},
                "signals": signals,
                "kind": "composite",
                "version": item["version"],
            }

        for key in selected:
            strategy_result = by_key[key]
            is_composite = key.startswith("composite:")
            event_run = run_historical_events(
                source_bars if is_composite else display_bars,
                strategy_result["signals"],
                strategy_id=(key.removeprefix("composite:") if is_composite else key),
                strategy_version=int(strategy_result.get("version", 1)),
                owner_id=owner_id,
                timeframe="1m" if is_composite else interval,
            )
            strategy_result["execution"] = {
                "engine": "deterministic_event_engine",
                "event_counts": event_run.event_counts,
                "events": event_run.execution_events,
            }

        snapshot_id = uuid4().hex
        replay_owner = request.state.auth_user
        if replay_owner.user_id != owner_id:
            replay_owner = replace(replay_owner, user_id=owner_id)
        trading_session = replay_trading.create(
            snapshot_id, replay_owner, display_bars
        )
        return {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "symbol": config.symbol,
            "trading_date": payload.trading_date.isoformat(),
            "session": payload.session,
            "interval": interval,
            "interval_name": TIMEFRAME_LABELS[interval],
            "bars": [
                {
                    "time": bar.time.isoformat(timespec="milliseconds"),
                    "end_time": bar.exchange_time.isoformat(timespec="milliseconds"),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "contract": bar.contract,
                    "session": bar.session,
                    "trading_date": bar.trading_date.isoformat(),
                    "no_trade": bar.no_trade,
                }
                for bar in display_bars
            ],
            "strategies": [by_key[key] for key in selected],
            "trading_session": trading_session.state(),
        }

    def replay_session(session_id: str, request: Request):
        try:
            return replay_trading.get(session_id, request_owner_id(request))
        except ReplaySessionNotFound as exc:
            raise HTTPException(status_code=404, detail="找不到回放交易 Session") from exc

    @app.get("/api/replay/sessions/{session_id}")
    def get_replay_session(session_id: str, request: Request):
        return replay_session(session_id, request).state()

    @app.put("/api/replay/sessions/{session_id}/cursor")
    def update_replay_cursor(
        session_id: str, payload: ReplayCursorUpdate, request: Request
    ):
        try:
            return replay_session(session_id, request).seek(payload.cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/replay/sessions/{session_id}/orders", status_code=201)
    def create_replay_order(
        session_id: str,
        payload: PaperOrderCreate,
        request: Request,
        idempotency_key: str = Header(
            ..., alias="Idempotency-Key", min_length=1, max_length=128
        ),
    ):
        try:
            order, created, state = replay_session(session_id, request).submit(
                PaperOrderCommand(
                    strategy_id=payload.strategy_id,
                    strategy_version=payload.strategy_version,
                    side=payload.side,
                    quantity=payload.quantity,
                    stop_loss_price=payload.stop_loss_price,
                    reduce_only=payload.reduce_only,
                    reason="manual_replay_order",
                ),
                idempotency_key=idempotency_key,
            )
            return {"mode": "replay", "created": created, "order": order, "session": state}
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/replay/sessions/{session_id}/reset")
    def reset_replay_session(session_id: str, request: Request):
        return replay_session(session_id, request).reset()

    @app.get("/api/backtest")
    def backtest(
        request: Request,
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
                parameters=repo.strategy_parameters(
                    request_owner_id(request)
                ).get(strategy.lower()),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/composite-backtest")
    def composite_backtest(
        strategy_id: str,
        request: Request,
        version: int | None = None,
        symbol: str = "TMF",
        start: date = Query(...),
        end: date = Query(...),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        item = repo.composite_strategy(
            strategy_id, version, request_owner_id(request)
        )
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
    def create_backtest_run(
        execution: BacktestExecutionRequest, request: Request
    ):
        owner_id = request_owner_id(request)
        if execution.strategy.startswith("composite:"):
            strategy_id = execution.strategy.removeprefix("composite:")
            item = repo.composite_strategy(
                strategy_id, execution.version, owner_id
            )
            if item is None:
                raise HTTPException(status_code=404, detail="找不到組合策略版本")
            result = composite_backtest(
                strategy_id=strategy_id,
                version=int(item["version"]),
                symbol=execution.symbol,
                start=execution.start,
                end=execution.end,
                request=request,
            )
            saved = repo.save_backtest_run(
                result, "composite", strategy_id, int(item["version"]),
                item["definition"], owner_id,
            )
        else:
            key = execution.strategy.lower()
            result = backtest(
                request=request,
                symbol=execution.symbol,
                strategy=key,
                interval=execution.interval,
                start=execution.start,
                end=execution.end,
            )
            snapshot = validate_strategy_parameters(
                key, repo.strategy_parameters(owner_id).get(key)
            )
            saved = repo.save_backtest_run(
                result, "atomic", key, None, snapshot, owner_id,
            )
        result["history_run_id"] = saved["run_id"]
        result["history_created_at"] = saved["created_at"]
        return result

    @app.get("/api/backtest-runs")
    def backtest_runs(
        request: Request,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
        strategy_key: str | None = None,
    ):
        runs = repo.backtest_runs(
            limit + 1, offset, strategy_key, request_owner_id(request)
        )
        return {
            "runs": runs[:limit],
            "has_more": len(runs) > limit,
            "limit": limit,
            "offset": offset,
        }

    @app.get("/api/backtest-runs/{run_id}")
    def backtest_run(run_id: str, request: Request):
        item = repo.backtest_run(run_id, request_owner_id(request))
        if item is None:
            raise HTTPException(status_code=404, detail="找不到回測紀錄")
        return item

    @app.delete("/api/backtest-runs/{run_id}")
    def delete_backtest_run(run_id: str, request: Request):
        item = repo.delete_backtest_run(run_id, request_owner_id(request))
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

    # Added last so it is the outermost application middleware and rejects
    # oversized bodies before authentication, JSON parsing, or route work.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_bytes=config.max_request_body_bytes,
    )
    return app
