from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from uuid import uuid4

from fastapi import APIRouter, Header, HTTPException, Query, Request

from ...backtest import MAX_BACKTEST_DAYS, run_composite_backtest, run_historical_events, run_strategy_backtest, validate_date_range
from ...market import SUPPORTED_TIMEFRAMES, TIMEFRAME_LABELS, aggregate_kbars, validate_timeframe
from ...paper import PaperOrderCommand
from ...replay import ReplaySessionNotFound
from ...strategy import SUPPORTED_STRATEGIES, analyze_strategies, generate_composite_signals, strategy_catalog, validate_strategy_parameters
from ..api_context import ApiDependencies
from ..api_models import BacktestExecutionRequest, PaperOrderCreate, ReplayCursorUpdate, ReplayPrepareRequest


def build_research_router(deps: ApiDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/backtest/options")
    def backtest_options(request: Request, symbol: str = "TMF"):
        if symbol.upper() != deps.config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        first, last = deps.repo.date_bounds(deps.config.symbol)
        owner_id = deps.request_owner_id(request)
        catalog = analyze_strategies([], SUPPORTED_STRATEGIES, parameters=deps.repo.strategy_parameters(owner_id))["strategies"]
        return {
            "symbol": deps.config.symbol,
            "available_start": first.isoformat() if first else None,
            "available_end": last.isoformat() if last else None,
            "max_days": MAX_BACKTEST_DAYS,
            "intervals": [{"key": key, "name": TIMEFRAME_LABELS[key]} for key in SUPPORTED_TIMEFRAMES],
            "strategies": [{"key": item["key"], "name": item["name"]} for item in catalog] + [
                {"key": f"composite:{item['id']}", "name": f"{item['name']} · v{item['version']}", "kind": "composite"}
                for item in deps.repo.composite_strategies(owner_id)
            ],
        }

    @router.get("/api/replay/options")
    def replay_options(request: Request, symbol: str = "TMF"):
        if symbol.upper() != deps.config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        first, last = deps.repo.date_bounds(deps.config.symbol)
        owner_id = deps.request_owner_id(request)
        catalog = strategy_catalog(deps.repo.strategy_parameters(owner_id))
        return {
            "symbol": deps.config.symbol,
            "available_start": first.isoformat() if first else None,
            "available_end": last.isoformat() if last else None,
            "available_dates": deps.repo.replay_availability(deps.config.symbol),
            "intervals": [{"key": key, "name": TIMEFRAME_LABELS[key]} for key in SUPPORTED_TIMEFRAMES if key not in {"1d", "1w"}],
            "strategies": [
                {"key": item["key"], "name": item["name"], "kind": "atomic", "color": item["color"]}
                for item in catalog
            ] + [
                {"key": f"composite:{item['id']}", "name": f"{item['name']} · v{item['version']}", "kind": "composite", "color": "#a78bfa"}
                for item in deps.repo.composite_strategies(owner_id)
            ],
            "max_strategies": 3,
            "sessions": [{"key": "day", "name": "日盤"}, {"key": "night", "name": "夜盤"}],
        }

    @router.post("/api/replay/prepare")
    def prepare_replay(payload: ReplayPrepareRequest, request: Request):
        if payload.symbol.upper() != deps.config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        selected = list(dict.fromkeys(value.strip().lower() for value in payload.strategies if value.strip()))
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
        owner_id = deps.request_owner_id(request)
        source_bars = [
            bar for bar in deps.repo.between_trading_dates(deps.config.symbol, payload.trading_date, payload.trading_date)
            if bar.session == payload.session
        ]
        if not source_bars:
            raise HTTPException(status_code=404, detail="所選交易日與時段沒有歷史 K 棒")
        display_bars = aggregate_kbars(source_bars, interval)
        atomic = [key for key in selected if not key.startswith("composite:")]
        unsupported = sorted(set(atomic) - set(SUPPORTED_STRATEGIES))
        if unsupported:
            raise HTTPException(status_code=400, detail=f"unsupported strategies: {', '.join(unsupported)}")
        results = analyze_strategies(
            display_bars, atomic, parameters=deps.repo.strategy_parameters(owner_id), interval=interval,
        )["strategies"]
        by_key = {str(item["key"]): item for item in results}
        for key in selected:
            if not key.startswith("composite:"):
                continue
            strategy_id = key.removeprefix("composite:")
            item = deps.repo.composite_strategy(strategy_id, owner_user_id=owner_id)
            if item is None or deps.repo.composite_strategy_archived(strategy_id, owner_id):
                raise HTTPException(status_code=404, detail="找不到可用的組合策略")
            signals, _trace = generate_composite_signals(source_bars, item["definition"])
            by_key[key] = {
                "key": key, "name": f"{item['name']} · v{item['version']}",
                "color": "#a78bfa", "parameters": {}, "signals": signals,
                "kind": "composite", "version": item["version"],
            }
        for key in selected:
            result = by_key[key]
            is_composite = key.startswith("composite:")
            event_run = run_historical_events(
                source_bars if is_composite else display_bars,
                result["signals"],
                strategy_id=key.removeprefix("composite:") if is_composite else key,
                strategy_version=int(result.get("version", 1)), owner_id=owner_id,
                timeframe="1m" if is_composite else interval,
            )
            result["execution"] = {
                "engine": "deterministic_event_engine",
                "event_counts": event_run.event_counts,
                "events": event_run.execution_events,
            }
        snapshot_id = uuid4().hex
        replay_owner = request.state.auth_user
        if replay_owner.user_id != owner_id:
            replay_owner = replace(replay_owner, user_id=owner_id)
        trading_session = deps.replay_trading.create(snapshot_id, replay_owner, display_bars)
        return {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "symbol": deps.config.symbol,
            "trading_date": payload.trading_date.isoformat(),
            "session": payload.session,
            "interval": interval,
            "interval_name": TIMEFRAME_LABELS[interval],
            "bars": [
                {
                    "time": bar.time.isoformat(timespec="milliseconds"),
                    "end_time": bar.exchange_time.isoformat(timespec="milliseconds"),
                    "open": bar.open, "high": bar.high, "low": bar.low, "close": bar.close,
                    "volume": bar.volume, "contract": bar.contract, "session": bar.session,
                    "trading_date": bar.trading_date.isoformat(), "no_trade": bar.no_trade,
                }
                for bar in display_bars
            ],
            "strategies": [by_key[key] for key in selected],
            "trading_session": trading_session.state(),
        }

    def replay_session(session_id: str, request: Request):
        try:
            return deps.replay_session(session_id, request)
        except ReplaySessionNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/api/replay/sessions/{session_id}")
    def get_replay_session(session_id: str, request: Request):
        return replay_session(session_id, request).state()

    @router.put("/api/replay/sessions/{session_id}/cursor")
    def update_replay_cursor(session_id: str, payload: ReplayCursorUpdate, request: Request):
        try:
            return replay_session(session_id, request).seek(payload.cursor)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/replay/sessions/{session_id}/orders", status_code=201)
    def create_replay_order(
        session_id: str, payload: PaperOrderCreate, request: Request,
        idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1, max_length=128),
    ):
        try:
            order, created, state = replay_session(session_id, request).submit(
                PaperOrderCommand(
                    strategy_id=payload.strategy_id, strategy_version=payload.strategy_version,
                    side=payload.side, quantity=payload.quantity,
                    stop_loss_price=payload.stop_loss_price, reduce_only=payload.reduce_only,
                    reason="manual_replay_order",
                ),
                idempotency_key=idempotency_key,
            )
            return {"mode": "replay", "created": created, "order": order, "session": state}
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/api/replay/sessions/{session_id}/reset")
    def reset_replay_session(session_id: str, request: Request):
        return replay_session(session_id, request).reset()

    def execute_atomic_backtest(
        request: Request, symbol: str, strategy: str, interval: str, start: date, end: date,
    ):
        if symbol.upper() != deps.config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        if strategy.lower() not in SUPPORTED_STRATEGIES:
            raise HTTPException(status_code=400, detail="unsupported strategy")
        try:
            selected_interval = validate_timeframe(interval)
            validate_date_range(start, end)
            bars = aggregate_kbars(deps.repo.between_trading_dates(deps.config.symbol, start, end), selected_interval)
            return run_strategy_backtest(
                bars, strategy.lower(), start, end, interval=selected_interval,
                parameters=deps.repo.strategy_parameters(deps.request_owner_id(request)).get(strategy.lower()),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/backtest")
    def backtest(
        request: Request, symbol: str = "TMF", strategy: str = "orb", interval: str = "1m",
        start: date = Query(...), end: date = Query(...),
    ):
        return execute_atomic_backtest(request, symbol, strategy, interval, start, end)

    def execute_composite_backtest(
        strategy_id: str, request: Request, version: int | None,
        symbol: str, start: date, end: date,
    ):
        if symbol.upper() != deps.config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        item = deps.repo.composite_strategy(strategy_id, version, deps.request_owner_id(request))
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        try:
            validate_date_range(start, end)
            return run_composite_backtest(
                deps.repo.between_trading_dates(deps.config.symbol, start, end),
                item["definition"], str(item["id"]), int(item["version"]), start, end,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/composite-backtest")
    def composite_backtest(
        strategy_id: str, request: Request, version: int | None = None,
        symbol: str = "TMF", start: date = Query(...), end: date = Query(...),
    ):
        return execute_composite_backtest(strategy_id, request, version, symbol, start, end)

    @router.post("/api/backtest-runs", status_code=201)
    def create_backtest_run(execution: BacktestExecutionRequest, request: Request):
        owner_id = deps.request_owner_id(request)
        if execution.strategy.startswith("composite:"):
            strategy_id = execution.strategy.removeprefix("composite:")
            item = deps.repo.composite_strategy(strategy_id, execution.version, owner_id)
            if item is None:
                raise HTTPException(status_code=404, detail="找不到組合策略版本")
            result = execute_composite_backtest(strategy_id, request, int(item["version"]), execution.symbol, execution.start, execution.end)
            saved = deps.repo.save_backtest_run(result, "composite", strategy_id, int(item["version"]), item["definition"], owner_id)
        else:
            key = execution.strategy.lower()
            result = execute_atomic_backtest(request, execution.symbol, key, execution.interval, execution.start, execution.end)
            snapshot = validate_strategy_parameters(key, deps.repo.strategy_parameters(owner_id).get(key))
            saved = deps.repo.save_backtest_run(result, "atomic", key, None, snapshot, owner_id)
        result["history_run_id"] = saved["run_id"]
        result["history_created_at"] = saved["created_at"]
        return result

    @router.get("/api/backtest-runs")
    def backtest_runs(
        request: Request, limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0), strategy_key: str | None = None,
    ):
        runs = deps.repo.backtest_runs(limit + 1, offset, strategy_key, deps.request_owner_id(request))
        return {"runs": runs[:limit], "has_more": len(runs) > limit, "limit": limit, "offset": offset}

    @router.get("/api/backtest-runs/{run_id}")
    def backtest_run(run_id: str, request: Request):
        item = deps.repo.backtest_run(run_id, deps.request_owner_id(request))
        if item is None:
            raise HTTPException(status_code=404, detail="找不到回測紀錄")
        return item

    @router.delete("/api/backtest-runs/{run_id}")
    def delete_backtest_run(run_id: str, request: Request):
        item = deps.repo.delete_backtest_run(run_id, deps.request_owner_id(request))
        if item is None:
            raise HTTPException(status_code=404, detail="找不到回測紀錄")
        return {
            "deleted_run_id": run_id, "strategy_key": item["strategy_key"],
            "strategy_version": item["strategy_version"],
            "released_strategy_reference": item["released_strategy_reference"],
        }

    return router
