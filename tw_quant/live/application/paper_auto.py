from __future__ import annotations

import hashlib
from typing import Mapping, Protocol

from ...auth import AccountStatus, AuthUser, TradingMode
from ...market import KBar
from ...paper import PaperOrderCommand, PaperTradingService
from ..storage import TradingRuntimeRepository


class UserDirectory(Protocol):
    def user_by_id(self, user_id: str) -> AuthUser | None: ...


class MarketHealth(Protocol):
    def status_message(self) -> dict[str, object]: ...


def auto_entry_idempotency_key(
    owner_id: str,
    runtime_id: str,
    contract: str,
    trigger_time: str,
    direction: str,
) -> str:
    canonical = "|".join(
        (owner_id, runtime_id, contract, trigger_time, direction, "entry")
    )
    return "auto:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PaperAutoEntryController:
    """Fail-closed bridge from durable entry decisions to Paper execution."""

    def __init__(
        self,
        runtimes: TradingRuntimeRepository,
        users: UserDirectory,
        market: MarketHealth,
        paper: PaperTradingService,
    ):
        self.runtimes = runtimes
        self.users = users
        self.market = market
        self.paper = paper

    @staticmethod
    def _stop_loss_pct(runtime: Mapping[str, object]) -> float:
        snapshot = runtime.get("strategy_snapshot")
        if not isinstance(snapshot, Mapping):
            raise ValueError("strategy_snapshot_missing")
        if runtime["strategy_kind"] == "atomic":
            values = snapshot.get("parameters")
        else:
            definition = snapshot.get("definition")
            values = definition.get("risk") if isinstance(definition, Mapping) else None
        if not isinstance(values, Mapping) or values.get("stop_loss_pct") is None:
            raise ValueError("stop_loss_pct_missing")
        value = float(values["stop_loss_pct"])
        if value <= 0:
            raise ValueError("stop_loss_pct_invalid")
        return value

    def _skip(self, decision: Mapping[str, object], reason: str) -> None:
        self.runtimes.update_decision_execution(
            str(decision["decision_id"]),
            str(decision["owner_user_id"]),
            {"execution_status": "skipped", "execution_reason": reason},
        )

    def _block_reason(
        self,
        runtime: Mapping[str, object],
        decision: Mapping[str, object],
    ) -> tuple[str | None, AuthUser | None]:
        if runtime.get("mode") != "paper_auto":
            return "paper_auto_required", None
        if runtime.get("status") != "armed":
            return f"runtime_{runtime.get('status', 'unavailable')}", None
        user = self.users.user_by_id(str(runtime["owner_user_id"]))
        if user is None:
            return "account_unavailable", None
        if user.status is not AccountStatus.ACTIVE:
            return "account_inactive", user
        if user.trading_mode is not TradingMode.PAPER:
            return "paper_mode_disabled", user
        if "orders.paper" not in user.permissions:
            return "permission_missing", user
        market = self.market.status_message()
        market_reason = market.get("trading_block_reason")
        if market_reason:
            return str(market_reason), user
        if market.get("service_status") not in {None, "healthy"}:
            return str(market["service_status"]), user
        account = self.paper.account(user.user_id)
        if account.get("recovery_status") != "healthy":
            return "recovery_degraded", user
        if account.get("kill_switch_active"):
            return "kill_switch_active", user
        runtime_id = str(runtime["runtime_id"])
        if any(
            position.get("runtime_id") == runtime_id
            and int(position.get("quantity", 0)) != 0
            for position in self.paper.positions(user.user_id)
        ):
            return "position_already_open", user
        if any(
            order.get("runtime_id") == runtime_id
            and order.get("status") in {"pending_risk", "approved"}
            and order.get("decision_id") != decision.get("decision_id")
            for order in self.paper.orders(user.user_id)
        ):
            return "entry_order_already_open", user
        return None, user

    def on_decision(
        self,
        runtime: Mapping[str, object],
        decision: Mapping[str, object],
        signal_bar: KBar,
    ) -> None:
        if decision.get("action") != "entry":
            return
        current = self.runtimes.trading_runtime(
            str(runtime["runtime_id"]), str(runtime["owner_user_id"])
        )
        if current is None:
            self._skip(decision, "runtime_unavailable")
            return
        reason, user = self._block_reason(current, decision)
        if reason is not None or user is None:
            self._skip(decision, reason or "account_unavailable")
            return
        try:
            reference = float(decision.get("reference_price") or signal_bar.close)
            stop_pct = self._stop_loss_pct(current)
            is_long = decision["direction"] == "long"
            planned_stop = reference * (1 - stop_pct if is_long else 1 + stop_pct)
            key = auto_entry_idempotency_key(
                user.user_id,
                str(current["runtime_id"]),
                str(decision["contract"]),
                str(decision["trigger_time"]),
                str(decision["direction"]),
            )
            order, _created = self.paper.submit(
                user,
                PaperOrderCommand(
                    strategy_id=str(current["strategy_id"]),
                    strategy_version=int(current.get("strategy_version") or 1),
                    side="buy" if is_long else "sell",
                    quantity=int(current["quantity"]),
                    stop_loss_price=planned_stop,
                    reason="strategy_auto_entry",
                    execution_timing="next_bar_open",
                    order_source="strategy_auto",
                    runtime_id=str(current["runtime_id"]),
                    decision_id=str(decision["decision_id"]),
                    reference_price=reference,
                ),
                idempotency_key=key,
                market_bar=signal_bar,
                occurred_at=signal_bar.received_time,
            )
            status = str(order["status"])
            self.runtimes.update_decision_execution(
                str(decision["decision_id"]), user.user_id,
                {
                    "execution_status": (
                        "submitted" if status == "approved" else "rejected"
                    ),
                    "execution_reason": order["status_reason"],
                    "order_id": order["order_id"],
                    "reference_price": reference,
                    "planned_stop_price": planned_stop,
                },
            )
        except Exception as exc:
            self._skip(decision, f"controller_error:{type(exc).__name__}")

    def after_bar(self, bar: KBar) -> None:
        if bar.status != "closed":
            return
        for decision in self.runtimes.auto_entry_decisions(
            bar.symbol, ("submitted",)
        ):
            order_id = decision.get("order_id")
            if not order_id:
                continue
            order = self.paper.order(
                str(decision["owner_user_id"]), str(order_id)
            )
            if order is None:
                continue
            if order.get("status") == "filled":
                fill = (
                    self.paper.fill(
                        str(decision["owner_user_id"]), str(order["fill_id"])
                    )
                    if order.get("fill_id") else None
                )
                self.runtimes.update_decision_execution(
                    str(decision["decision_id"]),
                    str(decision["owner_user_id"]),
                    {
                        "execution_status": "filled",
                        "execution_reason": "simulated_fill",
                        "actual_fill_price": fill.get("price") if fill else None,
                    },
                )
            elif order.get("status") == "rejected":
                self.runtimes.update_decision_execution(
                    str(decision["decision_id"]),
                    str(decision["owner_user_id"]),
                    {
                        "execution_status": "rejected",
                        "execution_reason": order.get("status_reason"),
                    },
                )
