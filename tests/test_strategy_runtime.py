from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tw_quant.auth import Role, SQLiteAuthRepository, TradingMode
from tw_quant.live.api import create_app
from tw_quant.live.application import TradingRuntimeApplicationService
from tw_quant.live.application.errors import ResourceNotFoundError
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import SQLiteBarRepository
from tw_quant.market import KBar
from tw_quant.paper import SQLitePaperRepository
from tw_quant.strategy import (
    analyze_strategies,
    evaluate_composite_intents,
    evaluate_strategy_intents,
    validate_composite_definition,
)


TAIPEI = ZoneInfo("Asia/Taipei")


def bar(
    minute: int,
    close: float,
    *,
    volume: int = 100,
    status: str = "closed",
) -> KBar:
    timestamp = datetime(2026, 9, 10, 15, 0, tzinfo=TAIPEI) + timedelta(
        minutes=minute
    )
    return KBar(
        symbol="TMF",
        contract="TMFU6",
        time=timestamp,
        open=close,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=volume,
        status=status,  # type: ignore[arg-type]
        session="night",
        trading_date=date(2026, 9, 11),
        first_tick_time=timestamp,
        last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=5),
        latency_ms=5,
    )


def orb_composite() -> dict[str, object]:
    return {
        "name": "Observe ORB",
        "description": "runtime test",
        "enabled": True,
        "direction": "both",
        "setup": {
            "operator": "all",
            "confirmation_window_minutes": 15,
            "rules": [],
        },
        "entry": {
            "operator": "all",
            "confirmation_window_minutes": 15,
            "rules": [{
                "strategy": "orb",
                "interval": "1m",
                "parameters": {
                    "opening_range_minutes": 2,
                    "volume_window": 2,
                    "volume_multiplier": 1,
                    "stop_loss_pct": 0.01,
                    "take_profit_pct": 0.03,
                },
            }],
        },
        "exit": {
            "operator": "any",
            "confirmation_window_minutes": 5,
            "rules": [],
        },
        "risk": {
            "monitor_interval": "1m",
            "stop_loss_pct": 0.01,
            "take_profit_pct": 0.03,
            "max_holding_minutes": 120,
        },
    }


class CanonicalIntentTests(unittest.TestCase):
    def test_closed_bar_intent_matches_backtest_trigger_time_and_direction(self):
        bars = [bar(index, 100.0) for index in range(20)]
        bars += [bar(20, 90.0), bar(21, 91.0)]

        intents = [
            item for item in evaluate_strategy_intents(bars, "bnf")
            if item["action"] == "entry"
        ]
        signal = analyze_strategies(bars, ["bnf"])["strategies"][0][
            "signals"
        ][0]

        self.assertEqual(intents[0]["trigger_time"], bars[20].time.isoformat(timespec="milliseconds"))
        self.assertEqual(intents[0]["trigger_time"], signal["trigger_time"])
        self.assertEqual(intents[0]["direction"], signal["direction"])
        self.assertEqual(intents[0]["context"], signal["context"])

    def test_forming_bar_never_confirms_an_intent(self):
        bars = [bar(index, 100.0) for index in range(20)]
        bars.append(bar(20, 90.0, status="forming"))
        entries = [
            item for item in evaluate_strategy_intents(bars, "bnf")
            if item["action"] == "entry"
        ]
        self.assertEqual(entries, [])

    def test_composite_intent_is_exposed_at_confirmation_not_fill(self):
        bars = [bar(0, 100), bar(1, 100), bar(2, 102, volume=500)]
        bars += [bar(3, 103), bar(4, 104)]
        definition = validate_composite_definition(orb_composite())
        intents = [
            item for item in evaluate_composite_intents(bars, definition)
            if item["action"] == "entry"
        ]
        self.assertEqual(intents[0]["trigger_time"], bars[3].time.isoformat(timespec="milliseconds"))


class TradingRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "runtime.sqlite3"
        self.repo = SQLiteBarRepository(self.path)
        self.service = TradingRuntimeApplicationService(
            self.repo, self.repo, self.repo, "TMF"
        )

    def tearDown(self):
        self.repo.close()
        self.temp.cleanup()

    def _seed_warmup(self) -> None:
        for index in range(20):
            self.repo.save(bar(index, 100.0))

    def test_decision_is_deduplicated_restart_safe_and_has_trigger_context(self):
        self._seed_warmup()
        runtime = self.service.create({
            "strategy_kind": "atomic",
            "strategy_id": "bnf",
            "symbol": "TMF",
            "interval": "1m",
            "quantity": 1,
            "mode": "observe",
        }, "owner-a")
        trigger = bar(20, 90.0)
        self.repo.save(trigger)
        self.service.on_bar(trigger)
        self.service.on_bar(trigger)

        fill = bar(21, 91.0)
        self.repo.save(fill)
        restarted = TradingRuntimeApplicationService(
            self.repo, self.repo, self.repo, "TMF"
        )
        restarted.on_bar(fill)
        decisions = self.repo.trading_decisions(
            str(runtime["runtime_id"]), "owner-a", 100
        )
        entries = [item for item in decisions if item["action"] == "entry"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["trigger_time"], trigger.time.isoformat(timespec="milliseconds"))
        self.assertEqual(entries[0]["source_bar_time"], entries[0]["trigger_time"])
        self.assertAlmostEqual(entries[0]["context"]["z_score"], -4.35889894)
        stored = self.repo.trading_runtime(
            str(runtime["runtime_id"]), "owner-a"
        )
        self.assertEqual(
            stored["last_evaluated_bar"],
            fill.time.isoformat(timespec="milliseconds"),
        )
        self.assertTrue(entries[0]["source_bar_id"].startswith("bar:"))

    def test_reconnect_records_historical_signal_without_executing_it(self):
        self._seed_warmup()
        runtime = self.service.create({
            "strategy_kind": "atomic",
            "strategy_id": "bnf",
            "symbol": "TMF",
            "interval": "1m",
            "quantity": 1,
            "mode": "paper_auto",
        }, "owner-a")
        runtime = self.service.arm(str(runtime["runtime_id"]), "owner-a")
        recovered_signal = bar(20, 90.0)
        reconnect_bar = bar(21, 91.0)
        self.repo.save(recovered_signal)
        self.repo.save(reconnect_bar)
        emitted: list[dict[str, object]] = []
        self.service.add_decision_listener(
            lambda _runtime, decision, _bar: emitted.append(dict(decision))
        )

        self.service.on_bar(reconnect_bar)

        decisions = self.repo.trading_decisions(
            str(runtime["runtime_id"]), "owner-a", 100
        )
        entry = next(item for item in decisions if item["action"] == "entry")
        self.assertEqual(entry["execution_status"], "skipped")
        self.assertEqual(
            entry["execution_reason"], "stale_or_recovered_signal"
        )
        self.assertFalse(any(
            item["action"] == "entry"
            and item["trigger_time"] == recovered_signal.time.isoformat(
                timespec="milliseconds"
            )
            for item in emitted
        ))

    def test_forming_bar_is_ignored_until_it_closes(self):
        self._seed_warmup()
        runtime = self.service.create({
            "strategy_kind": "atomic", "strategy_id": "bnf"
        }, "owner-a")
        forming = bar(20, 90.0, status="forming")
        self.repo.save(forming)
        self.service.on_bar(forming)
        self.assertEqual(self.repo.trading_decisions(
            str(runtime["runtime_id"]), "owner-a", 100
        ), [])

        closed = forming.copy(status="closed")
        self.repo.save(closed)
        self.service.on_bar(closed)
        self.assertTrue(self.repo.trading_decisions(
            str(runtime["runtime_id"]), "owner-a", 100
        ))

    def test_atomic_and_composite_snapshots_are_immutable(self):
        self.repo.save_strategy_parameters("bnf", {"mean_window": 20}, "owner-a")
        atomic = self.service.create({
            "strategy_kind": "atomic", "strategy_id": "bnf"
        }, "owner-a")
        self.repo.save_strategy_parameters("bnf", {"mean_window": 30}, "owner-a")
        self.assertEqual(atomic["strategy_snapshot"]["parameters"]["mean_window"], 20)

        definition = validate_composite_definition(orb_composite())
        saved_v1 = self.repo.save_composite_strategy(
            "combo", definition, "owner-a"
        )
        composite = self.service.create({
            "strategy_kind": "composite",
            "strategy_id": "combo",
            "strategy_version": 1,
        }, "owner-a")
        changed = dict(definition)
        changed["description"] = "version two"
        self.repo.save_composite_strategy("combo", changed, "owner-a")
        self.assertEqual(composite["strategy_version"], 1)
        self.assertEqual(composite["strategy_snapshot"]["definition"], saved_v1["definition"])

    def test_runtime_and_decisions_are_owner_scoped(self):
        runtime = self.service.create({
            "strategy_kind": "atomic", "strategy_id": "bnf"
        }, "owner-a")
        with self.assertRaises(ResourceNotFoundError):
            self.service.get(str(runtime["runtime_id"]), "owner-b")
        with self.assertRaises(ResourceNotFoundError):
            self.service.decisions(str(runtime["runtime_id"]), "owner-b")
        self.assertEqual(self.service.list("owner-b")["runtimes"], [])

    def test_observe_runtime_has_no_order_fill_or_position_side_effect(self):
        paper = SQLitePaperRepository(self.path)
        self._seed_warmup()
        runtime = self.service.create({
            "strategy_kind": "atomic", "strategy_id": "bnf"
        }, "owner-a")
        trigger = bar(20, 90.0)
        self.repo.save(trigger)
        self.service.on_bar(trigger)

        self.assertTrue(self.repo.trading_decisions(
            str(runtime["runtime_id"]), "owner-a", 100
        ))
        for table in (
            "paper_events",
            "paper_order_read_model",
            "paper_fill_read_model",
            "paper_position_read_model",
        ):
            count = paper.connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            self.assertEqual(count, 0, table)
        paper.close()


class TradingRuntimeRecoverySchemaTests(unittest.TestCase):
    def test_pr100_runtime_rows_survive_automatic_recovery_schema_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "pr100-runtime.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE strategy_runtimes (
                    runtime_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
                    strategy_kind TEXT NOT NULL, strategy_id TEXT NOT NULL,
                    strategy_version INTEGER, strategy_snapshot_json TEXT NOT NULL,
                    symbol TEXT NOT NULL, interval TEXT NOT NULL,
                    quantity INTEGER NOT NULL, mode TEXT NOT NULL,
                    status TEXT NOT NULL, last_evaluated_bar TEXT,
                    last_decision TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK(strategy_kind IN ('atomic', 'composite')),
                    CHECK(mode IN ('observe', 'paper_auto')),
                    CHECK(status IN ('active', 'paused', 'armed', 'stopped'))
                );
                CREATE TABLE trading_decisions (
                    decision_id TEXT PRIMARY KEY, runtime_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL, strategy_id TEXT NOT NULL,
                    strategy_version INTEGER, symbol TEXT NOT NULL,
                    contract TEXT NOT NULL, interval TEXT NOT NULL,
                    trigger_time TEXT NOT NULL, direction TEXT NOT NULL,
                    action TEXT NOT NULL, reason TEXT NOT NULL,
                    context_json TEXT NOT NULL, source_bar_time TEXT NOT NULL,
                    execution_status TEXT NOT NULL DEFAULT 'not_applicable',
                    execution_reason TEXT, order_id TEXT, reference_price REAL,
                    planned_stop_price REAL, actual_fill_price REAL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(runtime_id) REFERENCES strategy_runtimes(runtime_id)
                        ON DELETE CASCADE,
                    CHECK(direction IN ('long', 'short')),
                    CHECK(action IN ('entry', 'exit', 'none'))
                );
                """
            )
            snapshot = {
                "strategy": "bnf",
                "parameters": {"stop_loss_pct": 0.006},
                "interval": "1m",
            }
            connection.execute(
                "INSERT INTO strategy_runtimes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "runtime-1", "owner-a", "atomic", "bnf", None,
                    json.dumps(snapshot), "TMF", "1m", 2, "paper_auto",
                    "armed", "2026-09-10T15:20:00+08:00", "decision-1",
                    "2026-09-10T15:00:00+08:00",
                    "2026-09-10T15:20:00+08:00",
                ),
            )
            connection.execute(
                "INSERT INTO trading_decisions VALUES ("
                + ",".join("?" for _ in range(21))
                + ")",
                (
                    "decision-1", "runtime-1", "owner-a", "bnf", None,
                    "TMF", "TMFU6", "1m", "2026-09-10T15:20:00+08:00",
                    "long", "entry", "z_score_entry", '{"z_score": -4.0}',
                    "2026-09-10T15:20:00+08:00", "submitted", "risk_approved",
                    "order-1", 9000.0, 8946.0, None,
                    "2026-09-10T15:20:01+08:00",
                ),
            )
            connection.commit()
            connection.close()

            repository = SQLiteBarRepository(path)
            runtime = repository.trading_runtime("runtime-1", "owner-a")
            decision = repository.trading_decision("decision-1", "owner-a")
            self.assertEqual(runtime["status"], "armed")
            self.assertEqual(runtime["strategy_snapshot"], snapshot)
            self.assertIsNone(runtime["recovery_issue"])
            self.assertEqual(decision["execution_status"], "submitted")
            self.assertEqual(decision["order_id"], "order-1")
            self.assertEqual(decision["planned_stop_price"], 8946.0)
            self.assertIsNone(decision["source_bar_id"])
            repository.close()


class RuntimeFeed:
    provider_name = "runtime-test"
    contract = "TMFU6"

    async def start(self, _on_tick, on_status):
        on_status("connected")

    async def stop(self):
        return None

    async def heartbeat(self):
        return True


class TradingRuntimeApiTests(unittest.TestCase):
    def test_runtime_api_is_owner_scoped_and_paper_auto_starts_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime-api.sqlite3"
            identities = SQLiteAuthRepository(path)
            identities.create_user("a@example.com", role=Role.RESEARCHER)
            identities.create_user("b@example.com", role=Role.ADMIN)
            identities.create_user(
                "c@example.com", role=Role.TRADER,
                trading_mode=TradingMode.PAPER,
            )
            repository = SQLiteBarRepository(path)
            settings = LiveSettings(
                mode="mock",
                db_path=str(path),
                replay_csv="data/mock_tmf_ticks.csv",
                access_mode="cloudflare",
                cloudflare_access_team_domain="team.cloudflareaccess.com",
                cloudflare_access_audience="audience",
                authorization_mode="enforced",
                bootstrap_admin_emails=("b@example.com",),
            )
            app = create_app(
                settings,
                feed=RuntimeFeed(),
                repository=repository,
                auth_repository=identities,
            )
            owner_a = {
                "X-Authenticated-Subject": "subject-a",
                "X-Authenticated-Email": "a@example.com",
            }
            owner_b = {
                "X-Authenticated-Subject": "subject-b",
                "X-Authenticated-Email": "b@example.com",
            }
            owner_c = {
                "X-Authenticated-Subject": "subject-c",
                "X-Authenticated-Email": "c@example.com",
            }
            with TestClient(app) as client:
                created = client.post(
                    "/api/trading-runtimes",
                    headers=owner_a,
                    json={"strategy_id": "bnf", "mode": "observe"},
                )
                self.assertEqual(created.status_code, 201, created.text)
                runtime_id = created.json()["runtime_id"]
                self.assertEqual(created.json()["status"], "active")
                self.assertEqual(
                    client.get(
                        "/api/trading-runtimes", headers=owner_b
                    ).json()["runtimes"],
                    [],
                )
                self.assertEqual(client.get(
                    f"/api/trading-runtimes/{runtime_id}", headers=owner_b
                ).status_code, 404)
                self.assertEqual(client.get(
                    f"/api/trading-runtimes/{runtime_id}/decisions",
                    headers=owner_a,
                ).status_code, 200)
                paper_auto = client.post(
                    "/api/trading-runtimes",
                    headers=owner_a,
                    json={"strategy_id": "bnf", "mode": "paper_auto"},
                )
                self.assertEqual(paper_auto.status_code, 201)
                self.assertEqual(paper_auto.json()["status"], "paused")
                self.assertEqual(client.post(
                    f"/api/trading-runtimes/{paper_auto.json()['runtime_id']}/arm",
                    headers=owner_a,
                ).status_code, 403)
                trader_auto = client.post(
                    "/api/trading-runtimes", headers=owner_c,
                    json={"strategy_id": "bnf", "mode": "paper_auto"},
                )
                armed = client.post(
                    f"/api/trading-runtimes/{trader_auto.json()['runtime_id']}/arm",
                    headers=owner_c,
                )
                self.assertEqual(armed.status_code, 200, armed.text)
                self.assertEqual(armed.json()["status"], "armed")
                stopped = client.post(
                    f"/api/trading-runtimes/{runtime_id}/stop",
                    headers=owner_a,
                )
                self.assertEqual(stopped.status_code, 200)
                self.assertEqual(stopped.json()["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
