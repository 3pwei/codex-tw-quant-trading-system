from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from ...strategy import (
    SUPPORTED_STRATEGIES,
    default_composite_definition,
    generate_composite_signals,
    new_composite_id,
    strategy_catalog,
    validate_strategy_parameters,
)
from ..api_context import ApiDependencies
from ..api_models import CompositeStrategyPurge, CompositeStrategyUpdate, StrategyParametersUpdate
from ..storage import StrategyNameConflictError, StrategyPurgeError, StrategyReferencedError


def build_strategy_router(deps: ApiDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/strategies")
    def strategies_catalog(request: Request):
        return {"strategies": strategy_catalog(deps.repo.strategy_parameters(deps.request_owner_id(request)))}

    @router.put("/api/strategies/{strategy}")
    def update_strategy_parameters(strategy: str, update: StrategyParametersUpdate, request: Request):
        key = strategy.lower()
        if key not in SUPPORTED_STRATEGIES:
            raise HTTPException(status_code=404, detail="unsupported strategy")
        try:
            parameters = validate_strategy_parameters(key, update.parameters)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        owner_id = deps.request_owner_id(request)
        deps.repo.save_strategy_parameters(key, parameters, owner_id)
        return next(item for item in strategy_catalog(deps.repo.strategy_parameters(owner_id)) if item["key"] == key)

    @router.get("/api/composite-strategies")
    def composite_strategies(request: Request):
        owner_id = deps.request_owner_id(request)
        active = deps.repo.composite_strategies(owner_id)
        return {
            "template": default_composite_definition(),
            "strategies": active,
            "archived_strategies": deps.repo.archived_composite_strategies(owner_id),
            "reference_strategies": [
                {
                    "id": item["id"], "name": item["name"],
                    "versions": [
                        {"version": version["version"], "name": version["name"]}
                        for version in deps.repo.composite_strategy_versions(str(item["id"]), owner_id)
                    ],
                }
                for item in active
            ],
        }

    @router.get("/api/composite-strategies/{strategy_id}/versions")
    def composite_strategy_versions(strategy_id: str, request: Request):
        owner_id = deps.request_owner_id(request)
        versions = deps.repo.composite_strategy_versions(strategy_id, owner_id)
        if not versions:
            raise HTTPException(status_code=404, detail="找不到組合策略")
        return {"id": strategy_id, "archived": deps.repo.composite_strategy_archived(strategy_id, owner_id), "versions": versions}

    @router.get("/api/composite-strategies/{strategy_id}")
    def composite_strategy(strategy_id: str, request: Request, version: int | None = None):
        item = deps.repo.composite_strategy(strategy_id, version, deps.request_owner_id(request))
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        return item

    @router.get("/api/composite-strategy-signals/{strategy_id}")
    def composite_strategy_signals(
        strategy_id: str, request: Request, version: int | None = None,
        symbol: str = "TMF", limit: int = Query(5000, ge=20, le=5000),
    ):
        if symbol.upper() != deps.config.symbol:
            raise HTTPException(status_code=404, detail="unsupported symbol")
        item = deps.repo.composite_strategy(strategy_id, version, deps.request_owner_id(request))
        if item is None:
            raise HTTPException(status_code=404, detail="找不到組合策略版本")
        signals, trace = generate_composite_signals(deps.repo.latest(deps.config.symbol, limit), item["definition"])
        return {"id": item["id"], "version": item["version"], "name": item["name"], "signals": signals, "trace": trace}

    @router.post("/api/composite-strategies", status_code=201)
    def create_composite_strategy(update: CompositeStrategyUpdate, request: Request):
        owner_id = deps.request_owner_id(request)
        strategy_id = new_composite_id()
        try:
            definition = deps.validated_composite_for_save(update.definition, owner_id, strategy_id)
            return deps.repo.save_composite_strategy(strategy_id, definition, owner_id)
        except StrategyNameConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.put("/api/composite-strategies/{strategy_id}")
    def update_composite_strategy(strategy_id: str, update: CompositeStrategyUpdate, request: Request):
        owner_id = deps.request_owner_id(request)
        current = deps.repo.composite_strategy(strategy_id, owner_user_id=owner_id)
        if current is None:
            raise HTTPException(status_code=404, detail="找不到組合策略")
        if deps.repo.composite_strategy_archived(strategy_id, owner_id):
            raise HTTPException(status_code=410, detail="組合策略已封存")
        candidate_name = str(update.definition.get("name", "")).strip()
        target_id = new_composite_id() if candidate_name != current["name"] else strategy_id
        try:
            definition = deps.validated_composite_for_save(update.definition, owner_id, target_id)
            saved = deps.repo.save_composite_strategy(target_id, definition, owner_id)
        except StrategyNameConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if target_id != strategy_id:
            saved["created_from_strategy_id"] = strategy_id
        return saved

    @router.delete("/api/composite-strategies/{strategy_id}")
    def archive_composite_strategy(strategy_id: str, request: Request):
        try:
            return deps.repo.archive_composite_strategy(strategy_id, deps.request_owner_id(request))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/api/composite-strategies/purge")
    def purge_composite_strategies(purge: CompositeStrategyPurge, request: Request):
        try:
            return deps.repo.purge_archived_composite_strategies(purge.strategy_ids, deps.request_owner_id(request))
        except (StrategyReferencedError, StrategyPurgeError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
