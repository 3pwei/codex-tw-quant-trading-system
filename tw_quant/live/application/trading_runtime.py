from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Callable, Mapping

from ...market import (
    KBar,
    TIMEFRAME_MINUTES,
    aggregate_kbars,
    timeframe_bucket,
    validate_timeframe,
)
from ...strategy import (
    SUPPORTED_STRATEGIES,
    evaluate_composite_intents,
    evaluate_strategy_intents,
    validate_strategy_parameters,
)
from ..storage import (
    MarketRepository,
    StrategyRepository,
    TradingRuntimeRepository,
)
from .errors import InvalidInputError, ResourceNotFoundError


def decision_fingerprint(
    runtime_id: str,
    strategy_id: str,
    strategy_version: int | None,
    contract: str,
    trigger_time: str,
    action: str,
    direction: str,
) -> str:
    canonical = "|".join((
        runtime_id,
        strategy_id,
        str(strategy_version or "atomic"),
        contract,
        trigger_time,
        action,
        direction,
    ))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TradingRuntimeApplicationService:
    """Persist decisions produced from closed canonical bars."""

    def __init__(
        self,
        runtime_repository: TradingRuntimeRepository,
        strategy_repository: StrategyRepository,
        market_repository: MarketRepository,
        symbol: str,
        history_limit: int = 5000,
    ):
        self.runtime_repository = runtime_repository
        self.strategy_repository = strategy_repository
        self.market_repository = market_repository
        self.symbol = symbol
        self.history_limit = history_limit
        self._decision_listeners: list[
            Callable[[Mapping[str, object], Mapping[str, object], KBar], None]
        ] = []

    def add_decision_listener(
        self,
        listener: Callable[
            [Mapping[str, object], Mapping[str, object], KBar], None
        ],
    ) -> None:
        if listener not in self._decision_listeners:
            self._decision_listeners.append(listener)

    def create(
        self, request: Mapping[str, object], owner_id: str
    ) -> dict[str, object]:
        kind = str(request.get("strategy_kind", "atomic")).lower()
        if kind not in {"atomic", "composite"}:
            raise InvalidInputError("strategy_kind must be atomic or composite")
        mode = str(request.get("mode", "observe")).lower()
        if mode not in {"observe", "paper_auto"}:
            raise InvalidInputError("mode must be observe or paper_auto")
        symbol = str(request.get("symbol", self.symbol)).upper()
        if symbol != self.symbol:
            raise InvalidInputError("unsupported symbol")
        try:
            interval = validate_timeframe(str(request.get("interval", "1m")))
            quantity = int(request.get("quantity", 1))
        except (TypeError, ValueError) as exc:
            raise InvalidInputError(str(exc)) from exc
        if quantity < 1 or quantity > 100:
            raise InvalidInputError("quantity must be between 1 and 100")

        strategy_id = str(request.get("strategy_id", "")).strip().lower()
        strategy_version: int | None = None
        if kind == "atomic":
            if strategy_id not in SUPPORTED_STRATEGIES:
                raise InvalidInputError("unsupported atomic strategy")
            parameters = validate_strategy_parameters(
                strategy_id,
                self.strategy_repository.strategy_parameters(owner_id).get(
                    strategy_id
                ),
            )
            snapshot: dict[str, object] = {
                "strategy": strategy_id,
                "parameters": parameters,
                "interval": interval,
            }
        else:
            raw_version = request.get("strategy_version")
            try:
                requested_version = (
                    int(raw_version) if raw_version is not None else None
                )
            except (TypeError, ValueError) as exc:
                raise InvalidInputError(
                    "strategy_version must be an integer"
                ) from exc
            saved = self.strategy_repository.composite_strategy(
                strategy_id, requested_version, owner_id
            )
            if saved is None:
                raise ResourceNotFoundError("composite strategy not found")
            strategy_version = int(saved["version"])
            snapshot = {
                "strategy_id": strategy_id,
                "version": strategy_version,
                "definition": saved["definition"],
            }

        latest = [
            bar
            for bar in self.market_repository.latest(symbol, self.history_limit)
            if bar.status == "closed"
        ]
        initial_cursor = (
            latest[-1].time.isoformat(timespec="milliseconds")
            if latest
            else None
        )
        return self.runtime_repository.create_trading_runtime({
            "owner_user_id": owner_id,
            "strategy_kind": kind,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_snapshot": snapshot,
            "symbol": symbol,
            "interval": interval,
            "quantity": quantity,
            "mode": mode,
            "last_evaluated_bar": initial_cursor,
        })

    def list(self, owner_id: str) -> dict[str, object]:
        return {
            "runtimes": self.runtime_repository.trading_runtimes(owner_id)
        }

    def get(self, runtime_id: str, owner_id: str) -> dict[str, object]:
        runtime = self.runtime_repository.trading_runtime(
            runtime_id, owner_id
        )
        if runtime is None:
            raise ResourceNotFoundError("trading runtime not found")
        return runtime

    def stop(self, runtime_id: str, owner_id: str) -> dict[str, object]:
        runtime = self.runtime_repository.stop_trading_runtime(
            runtime_id, owner_id
        )
        if runtime is None:
            raise ResourceNotFoundError("trading runtime not found")
        return runtime

    def arm(self, runtime_id: str, owner_id: str) -> dict[str, object]:
        runtime = self.get(runtime_id, owner_id)
        if runtime["mode"] != "paper_auto":
            raise InvalidInputError("only paper_auto runtimes can be armed")
        if runtime["status"] == "stopped":
            raise InvalidInputError("a stopped runtime cannot be armed")
        latest = [
            bar for bar in self.market_repository.latest(
                self.symbol, self.history_limit
            )
            if bar.status == "closed"
        ]
        cursor = (
            latest[-1].time.isoformat(timespec="milliseconds")
            if latest else runtime.get("last_evaluated_bar")
        )
        result = self.runtime_repository.set_trading_runtime_status(
            runtime_id, owner_id, "armed", str(cursor) if cursor else None
        )
        assert result is not None
        return result

    def pause(self, runtime_id: str, owner_id: str) -> dict[str, object]:
        runtime = self.get(runtime_id, owner_id)
        if runtime["mode"] != "paper_auto":
            raise InvalidInputError("only paper_auto runtimes can be paused")
        if runtime["status"] == "stopped":
            raise InvalidInputError("a stopped runtime cannot be paused")
        result = self.runtime_repository.set_trading_runtime_status(
            runtime_id, owner_id, "paused"
        )
        assert result is not None
        return result

    def decisions(
        self, runtime_id: str, owner_id: str, limit: int = 200
    ) -> dict[str, object]:
        self.get(runtime_id, owner_id)
        return {
            "decisions": self.runtime_repository.trading_decisions(
                runtime_id, owner_id, limit
            )
        }

    @staticmethod
    def _completed_interval(bar: KBar, interval: str) -> bool:
        if interval == "1m":
            return True
        minutes = TIMEFRAME_MINUTES[validate_timeframe(interval)]
        if minutes is not None:
            following = bar.copy(time=bar.time + timedelta(minutes=1))
            return timeframe_bucket(bar, interval) != timeframe_bucket(
                following, interval
            )
        local_time = bar.time.timetz().replace(tzinfo=None)
        daily_close = (
            bar.session == "day"
            and local_time.hour == 13
            and local_time.minute == 44
        )
        if interval == "1d":
            return daily_close
        return daily_close and bar.trading_date.weekday() == 4

    def on_bar(self, bar: KBar) -> None:
        """LiveMarketService listener. It has no order or broker dependency."""
        if bar.status != "closed":
            return
        runtimes = self.runtime_repository.active_trading_runtimes(bar.symbol)
        if not runtimes:
            return
        source = [
            item
            for item in self.market_repository.latest(
                bar.symbol, self.history_limit
            )
            if item.status == "closed"
        ]
        intervals: dict[str, list[KBar]] = {}
        for runtime in runtimes:
            interval = str(runtime["interval"])
            if not self._completed_interval(bar, interval):
                continue
            if interval not in intervals:
                intervals[interval] = aggregate_kbars(source, interval)
            evaluated_bars = intervals[interval]
            if not evaluated_bars:
                continue
            cursor = runtime.get("last_evaluated_bar")
            pending_bars = [
                candidate
                for candidate in evaluated_bars
                if cursor is None
                or candidate.time.isoformat(timespec="milliseconds")
                > str(cursor)
            ]
            if not pending_bars:
                continue
            snapshot = runtime["strategy_snapshot"]
            assert isinstance(snapshot, Mapping)
            if runtime["strategy_kind"] == "atomic":
                raw_intents = evaluate_strategy_intents(
                    evaluated_bars,
                    str(snapshot["strategy"]),
                    parameters=dict(snapshot["parameters"]),
                    interval=interval,
                )
            else:
                raw_intents = evaluate_composite_intents(
                    source, snapshot["definition"]  # type: ignore[arg-type]
                )
            intents_by_time: dict[str, list[Mapping[str, object]]] = {}
            for intent in raw_intents:
                intents_by_time.setdefault(
                    str(intent["trigger_time"]), []
                ).append(intent)
            for evaluated in pending_bars:
                evaluated_time = evaluated.time.isoformat(
                    timespec="milliseconds"
                )
                decisions = [
                    self._decision(runtime, item, evaluated.close)
                    for item in intents_by_time.get(evaluated_time, [])
                ]
                inserted = self.runtime_repository.record_runtime_evaluation(
                    str(runtime["runtime_id"]), evaluated_time, decisions
                )
                for decision in inserted:
                    for listener in tuple(self._decision_listeners):
                        listener(runtime, decision, evaluated)

    @staticmethod
    def _decision(
        runtime: Mapping[str, object],
        intent: Mapping[str, object],
        reference_price: float,
    ) -> dict[str, object]:
        version = runtime.get("strategy_version")
        decision_id = decision_fingerprint(
            str(runtime["runtime_id"]),
            str(runtime["strategy_id"]),
            int(version) if version is not None else None,
            str(intent["contract"]),
            str(intent["trigger_time"]),
            str(intent["action"]),
            str(intent["direction"]),
        )
        decision = {
            "decision_id": decision_id,
            "runtime_id": runtime["runtime_id"],
            "owner_user_id": runtime["owner_user_id"],
            "strategy_id": runtime["strategy_id"],
            "strategy_version": version,
            "symbol": runtime["symbol"],
            "contract": intent["contract"],
            "interval": runtime["interval"],
            "trigger_time": intent["trigger_time"],
            "direction": intent["direction"],
            "action": intent["action"],
            "reason": intent["reason"],
            "context": dict(intent.get("context", {})),
            "source_bar_time": intent["source_bar_time"],
            "execution_status": (
                "pending"
                if runtime["mode"] == "paper_auto"
                and intent["action"] in {"entry", "exit"}
                else "not_applicable"
            ),
            "reference_price": reference_price,
        }
        return decision
