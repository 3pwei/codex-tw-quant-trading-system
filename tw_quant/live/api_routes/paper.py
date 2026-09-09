from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ...broker import BrokerOrderRequest, ExecutionMode
from ...paper import IdempotencyConflict
from ..api_context import ApiDependencies
from ..api_models import PaperControlRequest, PaperOrderCreate


def build_paper_router(deps: ApiDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/paper/account")
    def paper_account(request: Request):
        owner_id = request.state.auth_user.user_id
        return {"mode": "paper", "account": deps.paper.account(owner_id), "positions": deps.paper.positions(owner_id)}

    @router.get("/api/paper/orders")
    def paper_orders(request: Request):
        return {"orders": deps.paper.orders(request.state.auth_user.user_id)}

    @router.get("/api/paper/fills")
    def paper_fills(request: Request, limit: int = Query(200, ge=1, le=1000)):
        return {"fills": deps.paper.fills(request.state.auth_user.user_id)[:limit]}

    @router.get("/api/paper/events")
    def paper_events(request: Request, limit: int = Query(200, ge=1, le=1000)):
        return {"events": deps.paper.repository.events(request.state.auth_user.user_id, limit)}

    @router.post("/api/paper/orders", status_code=201)
    def create_paper_order(
        payload: PaperOrderCreate,
        request: Request,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    ):
        market = deps.service.status_message()
        block_reason = market.get("trading_block_reason")
        if not payload.reduce_only and block_reason:
            deps.paper.record_market_block()
            detail = (
                "market data provider is disconnected; new positions are blocked"
                if block_reason == "provider_disconnected"
                else "market data is stale; new positions are blocked"
            )
            raise HTTPException(status_code=503, detail=detail)
        latest = deps.repo.latest(deps.config.symbol, 1)
        if not latest:
            deps.paper.record_market_block()
            raise HTTPException(status_code=503, detail="market price is not available")
        quote_age = datetime.now(latest[0].received_time.tzinfo) - latest[0].received_time
        if quote_age > timedelta(seconds=deps.config.stale_after_seconds):
            deps.paper.record_market_block()
            raise HTTPException(status_code=503, detail="market price is stale")
        try:
            user = request.state.auth_user
            order, created = deps.paper.submit_request(
                user,
                BrokerOrderRequest(
                    client_order_id=idempotency_key.strip(), owner_id=user.user_id,
                    strategy_id=payload.strategy_id.strip(), strategy_version=payload.strategy_version,
                    symbol=latest[0].symbol, contract=latest[0].contract,
                    side=payload.side, quantity=payload.quantity, mode=ExecutionMode.PAPER,
                    reference_price=latest[0].close, risk_stop_price=payload.stop_loss_price,
                    reduce_only=payload.reduce_only,
                    purpose="exit" if payload.reduce_only else "entry",
                    reason="manual_paper_order",
                ),
                market_bar=latest[0],
            )
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"created": created, "order": order}

    @router.post("/api/paper/kill-switch")
    def activate_paper_kill_switch(payload: PaperControlRequest, request: Request):
        try:
            deps.paper.activate_kill_switch(request.state.auth_user.user_id, payload.reason.strip())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return deps.paper.account(request.state.auth_user.user_id)

    @router.post("/api/paper/kill-switch/reset")
    def reset_paper_kill_switch(payload: PaperControlRequest, request: Request):
        try:
            deps.paper.reset_kill_switch(request.state.auth_user.user_id, payload.reason.strip())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return deps.paper.account(request.state.auth_user.user_id)

    return router
