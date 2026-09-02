from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

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
from .sessions import TradingCalendar


def build_feed(settings: LiveSettings):
    if settings.mode == "mock":
        return ReplayFeed(settings.replay_csv, settings.replay_speed)
    return ShioajiFeed(
        api_key=settings.shioaji_api_key or "",
        secret_key=settings.shioaji_secret_key or "",
        contract=settings.contract,
        production=settings.shioaji_production,
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
        TradingCalendar(config.holidays),
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
        version="0.2.0",
        description="Quote-only Shioaji/Replay service; no order endpoints.",
        lifespan=lifespan,
    )
    app.state.market_service = service
    app.state.repository = repo
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET"],
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
