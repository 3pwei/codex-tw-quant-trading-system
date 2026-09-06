from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

from ..auth import AuthUser
from ..events import (
    BarClosedEvent,
    DeterministicEventEngine,
    EventMetadata,
    OrderIntent,
)
from ..execution import OrderRecord, SimulatedExecutionPipeline
from ..market import KBar, TAIPEI
from ..risk import AccountRiskGate, TradingAccessRegistry
from .repository import SQLitePaperRepository


@dataclass(frozen=True)
class PaperOrderCommand:
    strategy_id: str
    strategy_version: int
    side: Literal["buy", "sell"]
    quantity: int
    stop_loss_price: float | None
    reduce_only: bool = False
    reason: str = "manual_paper_order"

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if self.strategy_version < 1 or self.quantity < 1:
            raise ValueError("strategy_version and quantity must be positive")


class PaperTradingService:
    """In-process paper account; all identity and prices are server-derived."""

    def __init__(self, repository: SQLitePaperRepository):
        self.repository = repository
        self.engine = DeterministicEventEngine()
        self.access = TradingAccessRegistry()
        self.pipeline = SimulatedExecutionPipeline(execution_timing="current_close")
        self.risk = AccountRiskGate(self.pipeline.ledger, self.access)
        self.pipeline.risk = self.risk
        self.pipeline.install(self.engine)

    @staticmethod
    def _record(record: OrderRecord) -> dict[str, object]:
        order = record.intent
        return {
            "order_id": order.order_id,
            "submitted_at": order.meta.occurred_at.isoformat(timespec="milliseconds"),
            "strategy_id": order.strategy_id,
            "strategy_version": order.strategy_version,
            "symbol": order.symbol,
            "contract": order.contract,
            "side": order.side,
            "quantity": order.quantity,
            "reduce_only": order.reduce_only,
            "reference_price": order.reference_price,
            "stop_loss_price": order.stop_loss_price,
            "status": record.status,
            "status_reason": record.status_reason,
            "approved_quantity": record.approved_quantity,
            "fill_id": record.fill_id,
        }

    def _run(self) -> None:
        run = self.engine.run()
        self.repository.append_events([item.event for item in run.processed])

    def submit(
        self,
        user: AuthUser,
        command: PaperOrderCommand,
        *,
        idempotency_key: str,
        market_bar: KBar,
        occurred_at: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        key = idempotency_key.strip()
        if not key or len(key) > 128:
            raise ValueError("Idempotency-Key must contain 1 to 128 characters")
        existing_id = self.repository.order_for_key(user.user_id, key)
        if existing_id:
            existing = self.pipeline.broker.orders.get(existing_id)
            stored = self.repository.order_snapshot(user.user_id, existing_id)
            if existing is None and stored is None:
                raise RuntimeError("paper idempotency record has no matching order")
            if existing is not None:
                return self._record(existing), False
            assert stored is not None
            return stored, False

        self.access.set_user(user)
        submitted_at = occurred_at or datetime.now(TAIPEI)
        source_key = f"{user.user_id}:{key}"
        metadata = EventMetadata.create(
            kind="order_intent",
            occurred_at=submitted_at,
            source="paper_api",
            source_key=source_key,
            owner_id=user.user_id,
        )
        order = OrderIntent(
            meta=metadata,
            order_id=metadata.event_id,
            strategy_id=command.strategy_id,
            strategy_version=command.strategy_version,
            symbol=market_bar.symbol,
            contract=market_bar.contract,
            side=command.side,
            quantity=command.quantity,
            purpose="exit" if command.reduce_only else "entry",
            execution_timing="current_close",
            reduce_only=command.reduce_only,
            reason=command.reason,
            reference_price=market_bar.close,
            stop_loss_price=command.stop_loss_price,
            trading_date=market_bar.trading_date,
        )
        reserved_id = self.repository.reserve_key(user.user_id, key, order.order_id)
        if reserved_id != order.order_id:
            existing = self.pipeline.broker.orders.get(reserved_id)
            stored = self.repository.order_snapshot(user.user_id, reserved_id)
            if existing is None and stored is None:
                raise RuntimeError("paper idempotency record has no matching order")
            if existing is not None:
                return self._record(existing), False
            assert stored is not None
            return stored, False
        self.pipeline.broker.update_market_price(
            market_bar.symbol, market_bar.contract, market_bar.close
        )
        self.engine.publish(order)
        self._run()
        return self._record(self.pipeline.broker.orders[order.order_id]), True

    def on_bar(self, bar: KBar) -> None:
        if bar.status != "closed":
            return
        self.pipeline.broker.update_market_price(bar.symbol, bar.contract, bar.close)
        occurred_at = bar.received_time
        if self.engine.clock.current is not None and occurred_at < self.engine.clock.current:
            return
        event = BarClosedEvent(
            meta=EventMetadata.create(
                kind="bar_closed",
                occurred_at=occurred_at,
                source="paper_live_bars",
                source_key=f"{bar.contract}:1m:{bar.time.isoformat()}",
            ),
            symbol=bar.symbol,
            contract=bar.contract,
            timeframe="1m",
            open=bar.open,
            high=bar.high,
            low=bar.low,
            close=bar.close,
            volume=bar.volume,
            session=bar.session,
            trading_date=bar.trading_date,
        )
        self.engine.publish(event)
        self._run()

    def orders(self, owner_id: str) -> list[dict[str, object]]:
        live = [
            self._record(record)
            for record in reversed(tuple(self.pipeline.broker.orders.values()))
            if record.intent.meta.owner_id == owner_id
        ]
        known = {str(item["order_id"]) for item in live}
        stored_ids = [
            str(event["order_id"])
            for event in self.repository.events(owner_id, 10_000)
            if event["kind"] == "order_intent"
            and str(event["order_id"]) not in known
        ]
        stored = [
            snapshot
            for order_id in stored_ids
            if (snapshot := self.repository.order_snapshot(owner_id, order_id))
            is not None
        ]
        return live + stored

    def positions(self, owner_id: str) -> list[dict[str, object]]:
        latest_marks: dict[tuple[str, int, str, str], dict[str, object]] = {}
        for event in self.repository.events(owner_id, 10_000):
            if event["kind"] != "position":
                continue
            key = (
                str(event["strategy_id"]), int(event["strategy_version"]),
                str(event["symbol"]), str(event["contract"]),
            )
            latest_marks.setdefault(key, event)
        result = []
        for state in self.pipeline.ledger.open_positions():
            if state.key.owner_id != owner_id:
                continue
            item = {
                    **asdict(state.key),
                    "quantity": state.quantity,
                    "average_price": state.average_price,
                    "opened_at": state.opened_at.isoformat(timespec="milliseconds")
                    if state.opened_at else None,
                    "realized_pnl": state.realized_pnl,
                    "total_cost": state.total_cost,
                }
            mark = latest_marks.get((
                state.key.strategy_id, state.key.strategy_version,
                state.key.symbol, state.key.contract,
            ))
            item["unrealized_pnl"] = (
                float(mark["unrealized_pnl"]) if mark else 0.0
            )
            result.append(item)
        return result

    def fills(self, owner_id: str) -> list[dict[str, object]]:
        return [
            event for event in self.repository.events(owner_id)
            if event["kind"] == "fill"
        ]

    def account(self, owner_id: str) -> dict[str, object]:
        snapshot = asdict(self.risk.snapshot(owner_id))
        snapshot["trading_date"] = (
            snapshot["trading_date"].isoformat()
            if snapshot["trading_date"] else None
        )
        snapshot["cooldown_until"] = (
            snapshot["cooldown_until"].isoformat(timespec="milliseconds")
            if snapshot["cooldown_until"] else None
        )
        return snapshot

    def activate_kill_switch(self, owner_id: str, reason: str) -> None:
        occurred_at = datetime.now(TAIPEI)
        self.risk.activate_kill_switch(
            owner_id, reason, occurred_at=occurred_at, manual=True
        )
        self.repository.append_control(
            owner_id, "kill_switch.activated", reason, occurred_at
        )

    def reset_kill_switch(self, owner_id: str, reason: str) -> None:
        occurred_at = datetime.now(TAIPEI)
        self.risk.reset_kill_switch(
            owner_id, occurred_at=occurred_at, reason=reason
        )
        self.repository.append_control(
            owner_id, "kill_switch.reset", reason, occurred_at
        )

    def close(self) -> None:
        self.repository.close()
