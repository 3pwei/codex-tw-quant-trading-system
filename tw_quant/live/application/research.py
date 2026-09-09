from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal
from uuid import uuid4

from ...auth import AuthUser
from ...backtest import (
    MAX_BACKTEST_DAYS,
    run_composite_backtest,
    run_historical_events,
    run_strategy_backtest,
    validate_date_range,
)
from ...market import (
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_LABELS,
    aggregate_kbars,
    validate_timeframe,
)
from ...paper import PaperOrderCommand
from ...replay import ReplaySessionNotFound, ReplayTradingSessionRegistry
from ...strategy import (
    SUPPORTED_STRATEGIES,
    analyze_strategies,
    generate_composite_signals,
    strategy_catalog,
    validate_strategy_parameters,
)
from ..storage import BarRepository
from .errors import BadRequestError, InvalidInputError, ResourceNotFoundError


@dataclass(frozen=True)
class ReplayPrepareInput:
    symbol: str
    trading_date: date
    session: str
    interval: str
    strategies: tuple[str, ...]


@dataclass(frozen=True)
class ReplayOrderInput:
    strategy_id: str
    strategy_version: int
    side: Literal["buy", "sell"]
    quantity: int
    stop_loss_price: float | None
    reduce_only: bool


@dataclass(frozen=True)
class BacktestInput:
    symbol: str
    strategy: str
    interval: str
    start: date
    end: date
    version: int | None = None


class ResearchApplicationService:
    """Replay and backtest orchestration without transport concerns."""

    def __init__(
        self,
        repository: BarRepository,
        replay_sessions: ReplayTradingSessionRegistry,
        symbol: str,
    ):
        self.repository = repository
        self.replay_sessions = replay_sessions
        self.symbol = symbol

    def backtest_options(
        self, owner_id: str, symbol: str
    ) -> dict[str, object]:
        self._require_symbol(symbol)
        first, last = self.repository.date_bounds(self.symbol)
        catalog = analyze_strategies(
            [],
            SUPPORTED_STRATEGIES,
            parameters=self.repository.strategy_parameters(owner_id),
        )["strategies"]
        return {
            "symbol": self.symbol,
            "available_start": first.isoformat() if first else None,
            "available_end": last.isoformat() if last else None,
            "max_days": MAX_BACKTEST_DAYS,
            "intervals": [
                {"key": key, "name": TIMEFRAME_LABELS[key]}
                for key in SUPPORTED_TIMEFRAMES
            ],
            "strategies": [
                {"key": item["key"], "name": item["name"]}
                for item in catalog
            ]
            + [
                {
                    "key": f"composite:{item['id']}",
                    "name": f"{item['name']} · v{item['version']}",
                    "kind": "composite",
                }
                for item in self.repository.composite_strategies(owner_id)
            ],
        }

    def replay_options(
        self, owner_id: str, symbol: str
    ) -> dict[str, object]:
        self._require_symbol(symbol)
        first, last = self.repository.date_bounds(self.symbol)
        catalog = strategy_catalog(
            self.repository.strategy_parameters(owner_id)
        )
        return {
            "symbol": self.symbol,
            "available_start": first.isoformat() if first else None,
            "available_end": last.isoformat() if last else None,
            "available_dates": self.repository.replay_availability(self.symbol),
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
            ]
            + [
                {
                    "key": f"composite:{item['id']}",
                    "name": f"{item['name']} · v{item['version']}",
                    "kind": "composite",
                    "color": "#a78bfa",
                }
                for item in self.repository.composite_strategies(owner_id)
            ],
            "max_strategies": 3,
            "sessions": [
                {"key": "day", "name": "日盤"},
                {"key": "night", "name": "夜盤"},
            ],
        }

    def prepare_replay(
        self,
        preparation: ReplayPrepareInput,
        owner_id: str,
        replay_owner: AuthUser,
    ) -> dict[str, object]:
        self._require_symbol(preparation.symbol)
        selected = list(
            dict.fromkeys(
                value.strip().lower()
                for value in preparation.strategies
                if value.strip()
            )
        )
        if not selected:
            raise InvalidInputError("至少選擇一個策略")
        if len(selected) > 3:
            raise InvalidInputError("回放最多同時顯示 3 個策略")
        try:
            interval = validate_timeframe(preparation.interval)
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc
        if interval in {"1d", "1w"}:
            raise BadRequestError("回放僅支援 1 分鐘至 1 小時 K")

        source_bars = [
            bar
            for bar in self.repository.between_trading_dates(
                self.symbol,
                preparation.trading_date,
                preparation.trading_date,
            )
            if bar.session == preparation.session
        ]
        if not source_bars:
            raise ResourceNotFoundError("所選交易日與時段沒有歷史 K 棒")
        display_bars = aggregate_kbars(source_bars, interval)
        atomic = [
            key for key in selected if not key.startswith("composite:")
        ]
        unsupported = sorted(set(atomic) - set(SUPPORTED_STRATEGIES))
        if unsupported:
            raise BadRequestError(
                f"unsupported strategies: {', '.join(unsupported)}"
            )
        results = analyze_strategies(
            display_bars,
            atomic,
            parameters=self.repository.strategy_parameters(owner_id),
            interval=interval,
        )["strategies"]
        by_key = {str(item["key"]): item for item in results}
        for key in selected:
            if not key.startswith("composite:"):
                continue
            strategy_id = key.removeprefix("composite:")
            item = self.repository.composite_strategy(
                strategy_id, owner_user_id=owner_id
            )
            if item is None or self.repository.composite_strategy_archived(
                strategy_id, owner_id
            ):
                raise ResourceNotFoundError("找不到可用的組合策略")
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
            result = by_key[key]
            is_composite = key.startswith("composite:")
            event_run = run_historical_events(
                source_bars if is_composite else display_bars,
                result["signals"],
                strategy_id=(
                    key.removeprefix("composite:") if is_composite else key
                ),
                strategy_version=int(result.get("version", 1)),
                owner_id=owner_id,
                timeframe="1m" if is_composite else interval,
            )
            result["execution"] = {
                "engine": "deterministic_event_engine",
                "event_counts": event_run.event_counts,
                "events": event_run.execution_events,
            }

        snapshot_id = uuid4().hex
        if replay_owner.user_id != owner_id:
            replay_owner = replace(replay_owner, user_id=owner_id)
        trading_session = self.replay_sessions.create(
            snapshot_id, replay_owner, display_bars
        )
        return {
            "snapshot_id": snapshot_id,
            "created_at": datetime.now().astimezone().isoformat(
                timespec="seconds"
            ),
            "symbol": self.symbol,
            "trading_date": preparation.trading_date.isoformat(),
            "session": preparation.session,
            "interval": interval,
            "interval_name": TIMEFRAME_LABELS[interval],
            "bars": [self._bar_message(bar) for bar in display_bars],
            "strategies": [by_key[key] for key in selected],
            "trading_session": trading_session.state(),
        }

    def replay_state(self, session_id: str, owner_id: str) -> dict[str, object]:
        return self._session(session_id, owner_id).state()

    def seek_replay(
        self, session_id: str, cursor: int, owner_id: str
    ) -> dict[str, object]:
        try:
            return self._session(session_id, owner_id).seek(cursor)
        except ValueError as exc:
            raise InvalidInputError(str(exc)) from exc

    def submit_replay_order(
        self,
        session_id: str,
        order_input: ReplayOrderInput,
        idempotency_key: str,
        owner_id: str,
    ) -> dict[str, object]:
        try:
            command = PaperOrderCommand(
                strategy_id=order_input.strategy_id,
                strategy_version=order_input.strategy_version,
                side=order_input.side,
                quantity=order_input.quantity,
                stop_loss_price=order_input.stop_loss_price,
                reduce_only=order_input.reduce_only,
                reason="manual_replay_order",
            )
            order, created, state = self._session(
                session_id, owner_id
            ).submit(command, idempotency_key=idempotency_key)
        except (RuntimeError, ValueError) as exc:
            raise InvalidInputError(str(exc)) from exc
        return {
            "mode": "replay",
            "created": created,
            "order": order,
            "session": state,
        }

    def reset_replay(
        self, session_id: str, owner_id: str
    ) -> dict[str, object]:
        return self._session(session_id, owner_id).reset()

    def execute_atomic(
        self, execution: BacktestInput, owner_id: str
    ) -> dict[str, object]:
        self._require_symbol(execution.symbol)
        key = execution.strategy.lower()
        if key not in SUPPORTED_STRATEGIES:
            raise BadRequestError("unsupported strategy")
        try:
            interval = validate_timeframe(execution.interval)
            validate_date_range(execution.start, execution.end)
            bars = aggregate_kbars(
                self.repository.between_trading_dates(
                    self.symbol, execution.start, execution.end
                ),
                interval,
            )
            return run_strategy_backtest(
                bars,
                key,
                execution.start,
                execution.end,
                interval=interval,
                parameters=self.repository.strategy_parameters(owner_id).get(
                    key
                ),
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

    def execute_composite(
        self, execution: BacktestInput, owner_id: str
    ) -> dict[str, object]:
        self._require_symbol(execution.symbol)
        item = self.repository.composite_strategy(
            execution.strategy, execution.version, owner_id
        )
        if item is None:
            raise ResourceNotFoundError("找不到組合策略版本")
        try:
            validate_date_range(execution.start, execution.end)
            return run_composite_backtest(
                self.repository.between_trading_dates(
                    self.symbol, execution.start, execution.end
                ),
                item["definition"],
                str(item["id"]),
                int(item["version"]),
                execution.start,
                execution.end,
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

    def create_backtest_run(
        self, execution: BacktestInput, owner_id: str
    ) -> dict[str, object]:
        if execution.strategy.startswith("composite:"):
            strategy_id = execution.strategy.removeprefix("composite:")
            item = self.repository.composite_strategy(
                strategy_id, execution.version, owner_id
            )
            if item is None:
                raise ResourceNotFoundError("找不到組合策略版本")
            normalized = replace(
                execution,
                strategy=strategy_id,
                version=int(item["version"]),
            )
            result = self.execute_composite(normalized, owner_id)
            saved = self.repository.save_backtest_run(
                result,
                "composite",
                strategy_id,
                int(item["version"]),
                item["definition"],
                owner_id,
            )
        else:
            key = execution.strategy.lower()
            result = self.execute_atomic(replace(execution, strategy=key), owner_id)
            snapshot = validate_strategy_parameters(
                key, self.repository.strategy_parameters(owner_id).get(key)
            )
            saved = self.repository.save_backtest_run(
                result, "atomic", key, None, snapshot, owner_id
            )
        result["history_run_id"] = saved["run_id"]
        result["history_created_at"] = saved["created_at"]
        return result

    def backtest_runs(
        self,
        owner_id: str,
        limit: int,
        offset: int,
        strategy_key: str | None,
    ) -> dict[str, object]:
        runs = self.repository.backtest_runs(
            limit + 1, offset, strategy_key, owner_id
        )
        return {
            "runs": runs[:limit],
            "has_more": len(runs) > limit,
            "limit": limit,
            "offset": offset,
        }

    def backtest_run(self, run_id: str, owner_id: str) -> dict[str, object]:
        item = self.repository.backtest_run(run_id, owner_id)
        if item is None:
            raise ResourceNotFoundError("找不到回測紀錄")
        return item

    def delete_backtest_run(
        self, run_id: str, owner_id: str
    ) -> dict[str, object]:
        item = self.repository.delete_backtest_run(run_id, owner_id)
        if item is None:
            raise ResourceNotFoundError("找不到回測紀錄")
        return {
            "deleted_run_id": run_id,
            "strategy_key": item["strategy_key"],
            "strategy_version": item["strategy_version"],
            "released_strategy_reference": item[
                "released_strategy_reference"
            ],
        }

    def _session(self, session_id: str, owner_id: str):
        try:
            return self.replay_sessions.get(session_id, owner_id)
        except ReplaySessionNotFound as exc:
            raise ResourceNotFoundError("找不到回放交易 Session") from exc

    def _require_symbol(self, symbol: str) -> None:
        if symbol.upper() != self.symbol:
            raise ResourceNotFoundError("unsupported symbol")

    @staticmethod
    def _bar_message(bar) -> dict[str, object]:
        return {
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
