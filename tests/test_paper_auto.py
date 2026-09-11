from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from tw_quant.auth import AccountStatus, AuthUser, Role, TradingMode
from tw_quant.execution import SimulatedBroker
from tw_quant.live.application import (
    PaperAutoEntryController,
    TradingRuntimeApplicationService,
    auto_entry_idempotency_key,
)
from tw_quant.live.application.errors import InvalidInputError
from tw_quant.live.storage import SQLiteBarRepository
from tw_quant.market import KBar
from tw_quant.paper import PaperTradingService, SQLitePaperRepository


TAIPEI = ZoneInfo("Asia/Taipei")


def market_bar(minute: int, close: float, *, open_price: float | None = None) -> KBar:
    timestamp = datetime(2026, 9, 10, 15, 0, tzinfo=TAIPEI) + timedelta(minutes=minute)
    return KBar(
        symbol="TMF", contract="TMFU6", time=timestamp,
        open=open_price if open_price is not None else close,
        high=max(close, open_price or close) + 0.2,
        low=min(close, open_price or close) - 0.2,
        close=close, volume=100, status="closed", session="night",
        trading_date=date(2026, 9, 11), first_tick_time=timestamp,
        last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=5),
        latency_ms=5,
    )


class FakeUsers:
    def __init__(self, user: AuthUser):
        self.user = user

    def user_by_id(self, user_id: str) -> AuthUser | None:
        return self.user if user_id == self.user.user_id else None


class FakeMarket:
    def __init__(self):
        self.service_status = "healthy"
        self.block_reason: str | None = None

    def status_message(self) -> dict[str, object]:
        return {
            "service_status": self.service_status,
            "trading_block_reason": self.block_reason,
        }


class PaperAutoEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "paper-auto.sqlite3"
        self.repo = SQLiteBarRepository(self.path)
        self.paper = PaperTradingService(SQLitePaperRepository(self.path))
        self.user = AuthUser(
            user_id="owner-a", email="owner@example.com", role=Role.TRADER,
            status=AccountStatus.ACTIVE, trading_mode=TradingMode.PAPER,
            permissions=("orders.paper", "positions.read.own"),
        )
        self.users = FakeUsers(self.user)
        self.market = FakeMarket()
        self.runtime_service = TradingRuntimeApplicationService(
            self.repo, self.repo, self.repo, "TMF"
        )
        self.controller = PaperAutoEntryController(
            self.repo, self.users, self.market, self.paper
        )
        self.runtime_service.add_decision_listener(self.controller.on_decision)
        self.runtime = self.runtime_service.create(
            {"strategy_id": "bnf", "mode": "paper_auto", "quantity": 2},
            self.user.user_id,
        )
        self.runtime = self.runtime_service.arm(
            str(self.runtime["runtime_id"]), self.user.user_id
        )

    def tearDown(self):
        self.paper.close()
        self.repo.close()
        self.temp.cleanup()

    def _decision(self, minute: int = 20) -> tuple[dict[str, object], KBar]:
        bar = market_bar(minute, 90.0)
        trigger = bar.time.isoformat(timespec="milliseconds")
        decision = {
            "decision_id": f"decision-{minute}",
            "runtime_id": self.runtime["runtime_id"],
            "owner_user_id": self.user.user_id,
            "strategy_id": "bnf", "strategy_version": None,
            "symbol": "TMF", "contract": "TMFU6", "interval": "1m",
            "trigger_time": trigger, "direction": "long", "action": "entry",
            "reason": "z_score_entry", "context": {"z_score": -4.0},
            "source_bar_time": trigger, "execution_status": "pending",
            "reference_price": bar.close,
        }
        inserted = self.repo.record_runtime_evaluation(
            str(self.runtime["runtime_id"]), trigger, [decision]
        )
        self.assertEqual(len(inserted), 1)
        return decision, bar

    def test_entry_decision_routes_to_paper_and_fills_next_bar_open(self):
        decision, signal = self._decision()
        self.controller.on_decision(self.runtime, decision, signal)
        orders = self.paper.orders(self.user.user_id)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["status"], "approved")
        self.assertEqual(orders[0]["execution_timing"], "next_bar_open")
        self.assertEqual(orders[0]["order_source"], "strategy_auto")
        self.assertEqual(orders[0]["runtime_id"], self.runtime["runtime_id"])
        self.assertEqual(orders[0]["reference_price"], 90.0)
        self.assertAlmostEqual(orders[0]["stop_loss_price"], 89.46)

        next_bar = market_bar(21, 96.0, open_price=95.0)
        self.paper.on_bar(next_bar)
        self.controller.after_bar(next_bar)
        fill = self.paper.fills(self.user.user_id)[0]
        expected_fill = 95.0 + self.paper.pipeline.broker.costs.slippage_points
        self.assertAlmostEqual(fill["price"], expected_fill)
        stored = self.repo.trading_decisions(
            str(self.runtime["runtime_id"]), self.user.user_id, 10
        )[0]
        self.assertEqual(stored["execution_status"], "filled")
        self.assertEqual(stored["reference_price"], 90.0)
        self.assertAlmostEqual(stored["planned_stop_price"], 89.46)
        self.assertAlmostEqual(stored["actual_fill_price"], expected_fill)
        position = self.paper.positions(self.user.user_id)[0]
        self.assertEqual(position["runtime_id"], self.runtime["runtime_id"])
        self.assertEqual(position["decision_id"], decision["decision_id"])
        self.assertEqual(position["order_source"], "strategy_auto")

    def test_deterministic_idempotency_and_duplicate_callback_make_one_order(self):
        decision, signal = self._decision()
        first_key = auto_entry_idempotency_key(
            self.user.user_id, str(self.runtime["runtime_id"]), "TMFU6",
            str(decision["trigger_time"]), "long",
        )
        second_key = auto_entry_idempotency_key(
            self.user.user_id, str(self.runtime["runtime_id"]), "TMFU6",
            str(decision["trigger_time"]), "long",
        )
        self.assertEqual(first_key, second_key)
        self.controller.on_decision(self.runtime, decision, signal)
        self.controller.on_decision(self.runtime, decision, signal)
        self.assertEqual(len(self.paper.orders(self.user.user_id)), 1)

    def test_existing_runtime_position_skips_new_entry_without_pyramiding(self):
        first, signal = self._decision()
        self.controller.on_decision(self.runtime, first, signal)
        next_bar = market_bar(21, 91.0)
        self.paper.on_bar(next_bar)
        self.controller.after_bar(next_bar)
        second, second_bar = self._decision(22)
        self.controller.on_decision(self.runtime, second, second_bar)
        decisions = self.repo.trading_decisions(
            str(self.runtime["runtime_id"]), self.user.user_id, 10
        )
        newest = next(item for item in decisions if item["decision_id"] == "decision-22")
        self.assertEqual(newest["execution_status"], "skipped")
        self.assertEqual(newest["execution_reason"], "position_already_open")
        self.assertEqual(len(self.paper.orders(self.user.user_id)), 1)

    def test_fail_closed_market_account_permission_and_runtime_gates(self):
        cases = (
            ("market_stale", {"block_reason": "market_stale"}),
            ("provider_disconnected", {"block_reason": "provider_disconnected"}),
            ("recovery_degraded", {}),
            ("kill_switch_active", {}),
            ("permission_missing", {}),
            ("account_inactive", {}),
            ("paper_mode_disabled", {}),
        )
        for index, (expected, changes) in enumerate(cases, start=30):
            with self.subTest(reason=expected):
                decision, signal = self._decision(index)
                self.market.block_reason = changes.get("block_reason")
                original_user = self.users.user
                original_recovery = self.paper.recovery
                if expected == "permission_missing":
                    self.users.user = AuthUser(
                        **{**original_user.__dict__, "permissions": ()}
                    )
                elif expected == "account_inactive":
                    self.users.user = AuthUser(
                        **{**original_user.__dict__, "status": AccountStatus.SUSPENDED}
                    )
                elif expected == "paper_mode_disabled":
                    self.users.user = AuthUser(
                        **{**original_user.__dict__, "trading_mode": TradingMode.DISABLED}
                    )
                elif expected == "kill_switch_active":
                    self.paper.activate_kill_switch(self.user.user_id, "test")
                elif expected == "recovery_degraded":
                    self.paper.recovery = type(original_recovery)(
                        recovered_at=original_recovery.recovered_at,
                        restored_orders=0, restored_fills=0, restored_positions=0,
                        issues_by_owner={self.user.user_id: ("test",)}, duration_ms=0,
                    )
                self.controller.on_decision(self.runtime, decision, signal)
                stored = next(
                    item for item in self.repo.trading_decisions(
                        str(self.runtime["runtime_id"]), self.user.user_id, 100
                    ) if item["decision_id"] == decision["decision_id"]
                )
                self.assertEqual(stored["execution_reason"], expected)
                self.assertEqual(stored["execution_status"], "skipped")
                self.assertEqual(self.paper.orders(self.user.user_id), [])
                self.market.block_reason = None
                self.users.user = original_user
                self.paper.recovery = original_recovery
                if expected == "kill_switch_active":
                    self.paper.reset_kill_switch(self.user.user_id, "reset")

    def test_approved_next_open_order_survives_service_restart(self):
        decision, signal = self._decision()
        self.controller.on_decision(self.runtime, decision, signal)
        self.paper.close()
        self.paper = PaperTradingService(SQLitePaperRepository(self.path))
        self.controller = PaperAutoEntryController(
            self.repo, self.users, self.market, self.paper
        )
        next_bar = market_bar(21, 92.0, open_price=91.0)
        self.paper.on_bar(next_bar)
        self.controller.after_bar(next_bar)
        self.assertEqual(len(self.paper.fills(self.user.user_id)), 1)
        stored = self.repo.trading_decisions(
            str(self.runtime["runtime_id"]), self.user.user_id, 10
        )[0]
        self.assertEqual(stored["execution_status"], "filled")

    def test_auto_orders_are_owner_scoped_and_no_real_broker_is_used(self):
        decision, signal = self._decision()
        self.controller.on_decision(self.runtime, decision, signal)
        self.assertIsInstance(self.paper.pipeline.broker, SimulatedBroker)
        self.assertEqual(len(self.paper.orders(self.user.user_id)), 1)
        other_owner_orders = self.paper.orders("owner-b")
        self.assertEqual(other_owner_orders, [])

    def test_paused_and_stopped_runtime_cannot_execute(self):
        decision, signal = self._decision()
        self.runtime = self.runtime_service.pause(
            str(self.runtime["runtime_id"]), self.user.user_id
        )
        self.controller.on_decision(self.runtime, decision, signal)
        stored = self.repo.trading_decisions(
            str(self.runtime["runtime_id"]), self.user.user_id, 10
        )[0]
        self.assertEqual(stored["execution_reason"], "runtime_paused")
        stopped = self.runtime_service.stop(
            str(self.runtime["runtime_id"]), self.user.user_id
        )
        self.assertEqual(stopped["status"], "stopped")
        with self.assertRaisesRegex(InvalidInputError, "stopped runtime"):
            self.runtime_service.arm(
                str(self.runtime["runtime_id"]), self.user.user_id
            )
