from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from math import isclose
from time import perf_counter
from typing import Literal

from ..auth import AuthUser
from ..broker import BrokerOrderRequest, ExecutionMode, canonical_paper_status
from ..events import (
    BarClosedEvent,
    DeterministicEventEngine,
    EventMetadata,
    FillEvent,
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


@dataclass(frozen=True)
class PaperRecoveryReport:
    recovered_at: datetime
    restored_orders: int
    restored_fills: int
    restored_positions: int
    issues_by_owner: dict[str, tuple[str, ...]]
    duration_ms: float

    @property
    def healthy(self) -> bool:
        return not self.issues_by_owner


class IdempotencyConflict(ValueError):
    """The same request key was reused for different order semantics."""


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
        self.started_at = datetime.now(TAIPEI)
        self.submissions = 0
        self.filled_submissions = 0
        self.rejected_submissions = 0
        self.market_blocked_requests = 0
        self.last_submission_at: datetime | None = None
        self.total_submission_ms = 0.0
        self.max_submission_ms = 0.0
        self.recovery = self._recover()

    @staticmethod
    def _metadata(payload: dict[str, object]) -> EventMetadata:
        raw = payload.get("meta")
        if not isinstance(raw, dict):
            raise ValueError("event metadata is missing")
        return EventMetadata(
            event_id=str(raw["event_id"]),
            occurred_at=datetime.fromisoformat(str(raw["occurred_at"])),
            source=str(raw["source"]),
            correlation_id=(
                str(raw["correlation_id"]) if raw.get("correlation_id") else None
            ),
            causation_id=(
                str(raw["causation_id"]) if raw.get("causation_id") else None
            ),
            owner_id=str(raw["owner_id"]) if raw.get("owner_id") else None,
        )

    @classmethod
    def _fill_event(cls, payload: dict[str, object]) -> FillEvent:
        trading_date = payload.get("trading_date")
        return FillEvent(
            meta=cls._metadata(payload),
            fill_id=str(payload["fill_id"]),
            order_id=str(payload["order_id"]),
            strategy_id=str(payload["strategy_id"]),
            strategy_version=int(payload["strategy_version"]),
            symbol=str(payload["symbol"]),
            contract=str(payload["contract"]),
            side=str(payload["side"]),  # type: ignore[arg-type]
            quantity=int(payload["quantity"]),
            price=float(payload["price"]),
            commission=float(payload.get("commission", 0.0)),
            tax=float(payload.get("tax", 0.0)),
            slippage=float(payload.get("slippage", 0.0)),
            purpose=str(payload.get("purpose", "entry")),  # type: ignore[arg-type]
            reason=str(payload.get("reason", "simulated_fill")),
            trading_date=(
                datetime.fromisoformat(str(trading_date)).date()
                if trading_date else None
            ),
        )

    @staticmethod
    def _position_key(payload: dict[str, object]) -> tuple[str, int, str, str]:
        return (
            str(payload["strategy_id"]),
            int(payload["strategy_version"]),
            str(payload["symbol"]),
            str(payload["contract"]),
        )

    def events(self, owner_id: str, limit: int) -> list[dict[str, object]]:
        return self.repository.events(owner_id, limit)

    def _recover(self) -> PaperRecoveryReport:
        recovery_started = perf_counter()
        intents: dict[str, dict[str, dict[str, object]]] = {}
        decisions: dict[str, dict[str, dict[str, object]]] = {}
        fills: dict[str, set[str]] = {}
        latest_positions: dict[
            str, dict[tuple[str, int, str, str], dict[str, object]]
        ] = {}
        issues: dict[str, list[str]] = {}
        restored_fills = 0

        def issue(owner_id: str, code: str) -> None:
            owner_issues = issues.setdefault(owner_id, [])
            if code not in owner_issues:
                owner_issues.append(code)

        for record in self.repository.recovery_records():
            owner_id = str(record["owner_user_id"])
            if record["record_type"] == "control":
                try:
                    occurred_at = datetime.fromisoformat(str(record["occurred_at"]))
                    action = str(record["action"])
                    reason = str(record["reason"])
                    if action == "kill_switch.activated":
                        self.risk.activate_kill_switch(
                            owner_id, reason, occurred_at=occurred_at, manual=True
                        )
                    elif action == "kill_switch.reset":
                        self.risk.reset_kill_switch(
                            owner_id, occurred_at=occurred_at, reason=reason
                        )
                    else:
                        issue(owner_id, "unknown_control_action")
                except (TypeError, ValueError):
                    issue(owner_id, "invalid_control_record")
                continue

            payload = record.get("payload")
            if not isinstance(payload, dict):
                issue(owner_id, "invalid_event_payload")
                continue
            kind = str(payload.get("kind", ""))
            order_id = str(payload.get("order_id", ""))
            if kind == "order_intent":
                intents.setdefault(owner_id, {})[order_id] = payload
            elif kind == "risk_decision":
                decisions.setdefault(owner_id, {})[order_id] = payload
            elif kind == "fill":
                if order_id not in intents.get(owner_id, {}):
                    issue(owner_id, "fill_without_order")
                try:
                    fill = self._fill_event(payload)
                    if fill.meta.owner_id != owner_id:
                        issue(owner_id, "event_owner_mismatch")
                        continue
                    position_events = self.pipeline.ledger.on_fill(fill)
                    if position_events is not None:
                        self.risk.on_fill(fill)
                        restored_fills += 1
                    fills.setdefault(owner_id, set()).add(order_id)
                except (KeyError, TypeError, ValueError):
                    issue(owner_id, "invalid_fill_event")
            elif kind == "position":
                try:
                    latest_positions.setdefault(owner_id, {})[
                        self._position_key(payload)
                    ] = payload
                except (KeyError, TypeError, ValueError):
                    issue(owner_id, "invalid_position_event")

        for dangling in self.repository.dangling_idempotency():
            issue(str(dangling["owner_user_id"]), "dangling_idempotency_key")

        for owner_id, owner_intents in intents.items():
            for order_id, intent in owner_intents.items():
                decision = decisions.get(owner_id, {}).get(order_id)
                if decision is None:
                    issue(owner_id, "order_without_risk_decision")
                elif (
                    bool(decision.get("approved"))
                    and intent.get("execution_timing") == "current_close"
                    and order_id not in fills.get(owner_id, set())
                ):
                    issue(owner_id, "approved_order_without_fill")

        restored_positions = 0
        ledger_by_owner: dict[
            str, dict[tuple[str, int, str, str], object]
        ] = {}
        for state in self.pipeline.ledger.open_positions():
            ledger_by_owner.setdefault(state.key.owner_id, {})[
                (
                    state.key.strategy_id, state.key.strategy_version,
                    state.key.symbol, state.key.contract,
                )
            ] = state
            restored_positions += 1
        for owner_id in set(latest_positions) | set(ledger_by_owner):
            expected = latest_positions.get(owner_id, {})
            actual = ledger_by_owner.get(owner_id, {})
            for key in set(expected) | set(actual):
                snapshot = expected.get(key)
                state = actual.get(key)
                try:
                    expected_quantity = int(snapshot["quantity"]) if snapshot else 0
                except (KeyError, TypeError, ValueError):
                    issue(owner_id, "invalid_position_event")
                    continue
                actual_quantity = int(getattr(state, "quantity", 0))
                if expected_quantity != actual_quantity:
                    issue(owner_id, "position_quantity_mismatch")
                    continue
                if snapshot is None or state is None or actual_quantity == 0:
                    continue
                try:
                    comparisons = (
                        (float(snapshot["average_price"]), float(getattr(state, "average_price"))),
                        (float(snapshot["realized_pnl"]), float(getattr(state, "realized_pnl"))),
                        (float(snapshot.get("total_cost", 0.0)), float(getattr(state, "total_cost"))),
                    )
                except (KeyError, TypeError, ValueError):
                    issue(owner_id, "invalid_position_event")
                    continue
                if any(not isclose(left, right, abs_tol=0.001) for left, right in comparisons):
                    issue(owner_id, "position_value_mismatch")

        recovered_at = datetime.now(TAIPEI)
        for owner_id in issues:
            self.risk.activate_kill_switch(
                owner_id,
                "recovery_inconsistent",
                occurred_at=recovered_at,
                manual=True,
            )
        return PaperRecoveryReport(
            recovered_at=recovered_at,
            restored_orders=sum(len(items) for items in intents.values()),
            restored_fills=restored_fills,
            restored_positions=restored_positions,
            issues_by_owner={
                owner_id: tuple(owner_issues)
                for owner_id, owner_issues in issues.items()
            },
            duration_ms=(perf_counter() - recovery_started) * 1_000,
        )

    @staticmethod
    def _record(record: OrderRecord) -> dict[str, object]:
        order = record.intent
        return {
            "order_id": order.order_id,
            "client_order_id": order.client_order_id,
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
            "lifecycle_status": canonical_paper_status(record.status).value,
            "status_reason": record.status_reason,
            "approved_quantity": record.approved_quantity,
            "fill_id": record.fill_id,
        }

    def _run(self) -> None:
        run = self.engine.run()
        self.repository.append_events([item.event for item in run.processed])

    def _track_submission(
        self, order: dict[str, object], started: float
    ) -> dict[str, object]:
        elapsed_ms = (perf_counter() - started) * 1_000
        self.submissions += 1
        self.total_submission_ms += elapsed_ms
        self.max_submission_ms = max(self.max_submission_ms, elapsed_ms)
        self.last_submission_at = datetime.now(TAIPEI)
        if order.get("status") == "filled":
            self.filled_submissions += 1
        elif order.get("status") == "rejected":
            self.rejected_submissions += 1
        return order

    @staticmethod
    def _assert_idempotent_match(
        existing: dict[str, object], command: PaperOrderCommand
    ) -> None:
        expected = (
            command.strategy_id,
            command.strategy_version,
            command.side,
            command.quantity,
            command.reduce_only,
            command.stop_loss_price,
        )
        actual = (
            str(existing["strategy_id"]),
            int(existing["strategy_version"]),
            str(existing["side"]),
            int(existing["quantity"]),
            bool(existing["reduce_only"]),
            (
                float(existing["stop_loss_price"])
                if existing.get("stop_loss_price") is not None else None
            ),
        )
        if actual != expected:
            raise IdempotencyConflict(
                "Idempotency-Key was already used for a different paper order"
            )

    def submit(
        self,
        user: AuthUser,
        command: PaperOrderCommand,
        *,
        idempotency_key: str,
        market_bar: KBar,
        occurred_at: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        started = perf_counter()
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
                record = self._record(existing)
                self._assert_idempotent_match(record, command)
                return self._track_submission(record, started), False
            assert stored is not None
            self._assert_idempotent_match(stored, command)
            return self._track_submission(stored, started), False

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
            client_order_id=key,
        )
        reserved_id = self.repository.reserve_key(user.user_id, key, order.order_id)
        if reserved_id != order.order_id:
            existing = self.pipeline.broker.orders.get(reserved_id)
            stored = self.repository.order_snapshot(user.user_id, reserved_id)
            if existing is None and stored is None:
                raise RuntimeError("paper idempotency record has no matching order")
            if existing is not None:
                record = self._record(existing)
                self._assert_idempotent_match(record, command)
                return self._track_submission(record, started), False
            assert stored is not None
            self._assert_idempotent_match(stored, command)
            return self._track_submission(stored, started), False
        self.pipeline.broker.update_market_price(
            market_bar.symbol, market_bar.contract, market_bar.close
        )
        self.engine.publish(order)
        self._run()
        result = self._record(self.pipeline.broker.orders[order.order_id])
        return self._track_submission(result, started), True

    def submit_request(
        self,
        user: AuthUser,
        request: BrokerOrderRequest,
        *,
        market_bar: KBar,
        occurred_at: datetime | None = None,
    ) -> tuple[dict[str, object], bool]:
        """Execute the canonical broker request through the Paper use case."""
        if request.mode is not ExecutionMode.PAPER:
            raise ValueError("PaperTradingService only accepts paper orders")
        if request.owner_id != user.user_id:
            raise ValueError("paper order owner does not match authenticated user")
        if (
            request.symbol != market_bar.symbol
            or request.contract != market_bar.contract
        ):
            raise ValueError("paper order contract does not match server market data")
        if request.order_type != "market":
            raise ValueError("Paper Trading currently supports market orders only")
        if (
            request.reference_price is not None
            and request.reference_price != market_bar.close
        ):
            raise ValueError("paper reference price must be server-derived")
        return self.submit(
            user,
            PaperOrderCommand(
                strategy_id=request.strategy_id,
                strategy_version=request.strategy_version,
                side=request.side,
                quantity=request.quantity,
                stop_loss_price=request.risk_stop_price,
                reduce_only=request.reduce_only,
                reason=request.reason,
            ),
            idempotency_key=request.client_order_id,
            market_bar=market_bar,
            occurred_at=occurred_at,
        )

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
        return self.repository.orders(owner_id)

    def positions(self, owner_id: str) -> list[dict[str, object]]:
        return self.repository.positions(owner_id)

    def fills(
        self, owner_id: str, limit: int = 500
    ) -> list[dict[str, object]]:
        return self.repository.fills(owner_id, limit)

    def fill(
        self, owner_id: str, fill_id: str
    ) -> dict[str, object] | None:
        return self.repository.fill_snapshot(owner_id, fill_id)

    def order_for_client_id(
        self, owner_id: str, client_order_id: str
    ) -> dict[str, object] | None:
        return self.repository.order_for_client_id(owner_id, client_order_id)

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
        owner_issues = self.recovery.issues_by_owner.get(owner_id, ())
        snapshot["recovery_status"] = "degraded" if owner_issues else "healthy"
        snapshot["recovery_issues"] = list(owner_issues)
        snapshot["recovered_at"] = self.recovery.recovered_at.isoformat(
            timespec="milliseconds"
        )
        return snapshot

    def health(self) -> dict[str, object]:
        repository = self.repository.stats()
        kill_switches = self.risk.kill_switch_summary()
        return {
            "status": "healthy" if self.recovery.healthy else "degraded",
            "started_at": self.started_at.isoformat(timespec="milliseconds"),
            "recovered_at": self.recovery.recovered_at.isoformat(
                timespec="milliseconds"
            ),
            "restored_orders": self.recovery.restored_orders,
            "restored_fills": self.recovery.restored_fills,
            "restored_positions": self.recovery.restored_positions,
            "recovery_duration_ms": round(self.recovery.duration_ms, 3),
            "inconsistent_owners": len(self.recovery.issues_by_owner),
            "recovery_issue_count": sum(
                len(items) for items in self.recovery.issues_by_owner.values()
            ),
            "submission_requests": self.submissions,
            "filled_submissions": self.filled_submissions,
            "rejected_submissions": self.rejected_submissions,
            "market_blocked_requests": self.market_blocked_requests,
            "order_requests": self.submissions + self.market_blocked_requests,
            "active_kill_switches": kill_switches["active"],
            "manual_kill_switches": kill_switches["manual"],
            "automatic_kill_switches": kill_switches["automatic"],
            "average_submission_ms": round(
                self.total_submission_ms / self.submissions, 3
            ) if self.submissions else None,
            "max_submission_ms": round(self.max_submission_ms, 3),
            "last_submission_at": self.last_submission_at.isoformat(
                timespec="milliseconds"
            ) if self.last_submission_at else None,
            "repository": repository,
        }

    def record_market_block(self) -> None:
        self.market_blocked_requests += 1

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
