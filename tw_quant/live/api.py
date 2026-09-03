from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..backtest import MAX_BACKTEST_DAYS, run_strategy_backtest, validate_date_range
from ..market import TradingCalendar
from ..strategy import (
    SUPPORTED_STRATEGIES,
    analyze_strategies,
    strategy_catalog,
    validate_strategy_parameters,
)
from .access import (
    AccessTokenError,
    AccessValidator,
    CloudflareAccessValidator,
    DisabledAccessValidator,
)
from .feed import ReplayFeed, ShioajiFeed
from .service import LiveMarketService
from .settings import LiveSettings
from .storage import BarRepository, SQLiteBarRepository


class StrategyParametersUpdate(BaseModel):
    parameters: dict[str, object]


def build_feed(settings: LiveSettings):
    if settings.mode == "mock":
        return ReplayFeed(settings.replay_csv, settings.replay_speed)
    return ShioajiFeed(
        api_key=settings.shioaji_api_key or "",
        secret_key=settings.shioaji_secret_key or "",
        contract=settings.contract,
        production=settings.shioaji_production,
        history_days=settings.history_days,
    )


def create_app(
    settings: LiveSettings | None = None,
    feed=None,
    repository: BarRepository | None = None,
    access_validator: AccessValidator | None = None,
) -> FastAPI:
    config = settings or LiveSettings.from_env()
    config.validate()
    repo = repository or SQLiteBarRepository(config.db_path)
    market_feed = feed or build_feed(config)
    validator = access_validator
    if validator is None:
        if config.access_mode == "cloudflare":
            validator = CloudflareAccessValidator(
                config.cloudflare_access_team_domain or "",
                config.cloudflare_access_audience or "",
            )
        else:
            validator = DisabledAccessValidator()
    service = LiveMarketService(
        market_feed, repo, config.symbol, config.heartbeat_seconds,
        TradingCalendar(config.holidays), config.history_limit,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await service.start()
        try:
            yield
        finally:
            await service.stop()
            repo.close()

    app = FastAPI(
        title="TMF Live Market API",
        version="0.4.0",
        description="Quote-only Shioaji/Replay service; no order endpoints.",
        lifespan=lifespan,
    )
    app.state.market_service = service
    app.state.repository = repo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "PUT", "OPTIONS"],
        allow_headers=["*"],
    )

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
        headers = {"X-Authenticated-Subject": identity.subject}
        if identity.email:
            headers["X-Authenticated-Email"] = identity.email
        return Response(status_code=204, headers=headers)

    @app.get("/api/health")
    async def health():
        return service.status_message()

    @app.get("/api/kbars")
    async def kbars(
        symbol: str = "TMF",
        interval: str = "1m",
        limit: int = Query(500, ge=1, le=5000),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        if interval != "1m":
            raise HTTPException(status_code=400, detail="only interval=1m is supported")
        return [
            bar.to_message(service.connection_status)
            for bar in repo.latest(config.symbol, limit)
        ]

    @app.get("/api/strategy-signals")
    async def strategy_signals(
        symbol: str = "TMF",
        strategies: str = "orb,bnf",
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
        return analyze_strategies(
            repo.latest(config.symbol, limit),
            selected,
            parameters=repo.strategy_parameters(),
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
            "strategies": [
                {"key": item["key"], "name": item["name"]} for item in catalog
            ],
        }

    @app.get("/api/backtest")
    def backtest(
        symbol: str = "TMF",
        strategy: str = "orb",
        start: date = Query(...),
        end: date = Query(...),
    ):
        if symbol.upper() != config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        if strategy.lower() not in SUPPORTED_STRATEGIES:
            raise HTTPException(status_code=400, detail="unsupported strategy")
        try:
            validate_date_range(start, end)
            bars = repo.between_trading_dates(config.symbol, start, end)
            return run_strategy_backtest(
                bars,
                strategy.lower(),
                start,
                end,
                parameters=repo.strategy_parameters().get(strategy.lower()),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.websocket("/ws/market/{symbol}")
    async def market_socket(websocket: WebSocket, symbol: str):
        if symbol.upper() != config.symbol:
            await websocket.close(code=1008, reason="unsupported symbol")
            return
        await websocket.accept()
        queue = service.hub.subscribe()
        await websocket.send_json(service.status_message())
        try:
            while True:
                await websocket.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            service.hub.unsubscribe(queue)

    return app


app = create_app()
