from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tw_quant.auth import Role, SQLiteAuthRepository
from tw_quant.live.api import create_app
from tw_quant.live.feed import ReplayFeed
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import DEFAULT_OWNER_ID, SQLiteBarRepository
from tw_quant.market import KBar
from tw_quant.strategy import default_composite_definition

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")


def market_bar() -> KBar:
    timestamp = datetime(2026, 8, 24, 15, 0, tzinfo=TAIPEI)
    return KBar(
        symbol="TMF",
        contract="TMFU6",
        time=timestamp,
        open=100,
        high=101,
        low=99,
        close=100,
        volume=10,
        status="closed",
        session="night",
        trading_date=date(2026, 8, 25),
        first_tick_time=timestamp,
        last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=5),
        latency_ms=5,
    )


def backtest_result(strategy_id: str) -> dict[str, object]:
    return {
        "metadata": {
            "symbol": "TMF",
            "strategy": "owner test",
            "strategy_key": strategy_id,
            "strategy_version": 1,
            "interval": "多週期",
            "interval_key": "multi",
            "date_range": "2026-08-25 ～ 2026-08-25",
        },
        "config": {},
        "summary": {"net_profit": 0},
        "trades": [],
        "equity": [],
        "bars": [],
    }


class OwnershipRepositoryTests(unittest.TestCase):
    def test_strategies_parameters_and_backtests_are_owner_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "ownership.sqlite3")
            try:
                owner_a = "user-a"
                owner_b = "user-b"
                definition = default_composite_definition()
                repo.save_strategy_parameters(
                    "orb", {"opening_range_minutes": 3}, owner_a
                )
                repo.save_strategy_parameters(
                    "orb", {"opening_range_minutes": 20}, owner_b
                )
                repo.save_composite_strategy("combo-a", definition, owner_a)
                repo.save_composite_strategy("combo-b", definition, owner_b)
                saved = repo.save_backtest_run(
                    backtest_result("combo-a"),
                    "composite",
                    "combo-a",
                    1,
                    definition,
                    owner_a,
                )

                self.assertEqual(
                    repo.strategy_parameters(owner_a)["orb"][
                        "opening_range_minutes"
                    ],
                    3,
                )
                self.assertEqual(
                    repo.strategy_parameters(owner_b)["orb"][
                        "opening_range_minutes"
                    ],
                    20,
                )
                self.assertEqual(
                    [item["id"] for item in repo.composite_strategies(owner_a)],
                    ["combo-a"],
                )
                self.assertIsNone(
                    repo.composite_strategy("combo-a", owner_user_id=owner_b)
                )
                self.assertEqual(repo.backtest_runs(100, 0, owner_user_id=owner_b), [])
                self.assertIsNone(repo.backtest_run(saved["run_id"], owner_b))
                self.assertIsNone(repo.delete_backtest_run(saved["run_id"], owner_b))
                self.assertIsNotNone(repo.backtest_run(saved["run_id"], owner_a))
            finally:
                repo.close()

    def test_legacy_data_is_claimed_once_by_bootstrap_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-owner.sqlite3"
            definition = default_composite_definition()
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE strategy_parameters (
                    strategy TEXT PRIMARY KEY,
                    parameters_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE composite_strategies (
                    strategy_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    definition_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, version)
                );
                CREATE TABLE archived_composite_strategies (
                    strategy_id TEXT PRIMARY KEY,
                    archived_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO strategy_parameters VALUES (?,?,?)",
                ("orb", '{"legacy": 1}', "2026-09-01T00:00:00+08:00"),
            )
            connection.execute(
                "INSERT INTO composite_strategies VALUES (?,?,?,?,?)",
                (
                    "legacy-combo", 1, definition["name"],
                    json.dumps(definition, ensure_ascii=False),
                    "2026-09-01T00:00:00+08:00",
                ),
            )
            connection.commit()
            connection.close()
            repo = SQLiteBarRepository(path)
            try:
                saved = repo.save_backtest_run(
                    backtest_result("legacy-combo"),
                    "composite",
                    "legacy-combo",
                    1,
                    definition,
                )

                repo.claim_legacy_ownership("bootstrap-admin")

                self.assertEqual(repo.strategy_parameters(DEFAULT_OWNER_ID), {})
                self.assertEqual(
                    repo.composite_strategies(DEFAULT_OWNER_ID), []
                )
                self.assertIn(
                    "orb", repo.strategy_parameters("bootstrap-admin")
                )
                self.assertIsNotNone(
                    repo.composite_strategy(
                        "legacy-combo", owner_user_id="bootstrap-admin"
                    )
                )
                self.assertIsNotNone(
                    repo.backtest_run(saved["run_id"], "bootstrap-admin")
                )
                repo.claim_legacy_ownership("different-admin")
                self.assertIsNotNone(
                    repo.backtest_run(saved["run_id"], "bootstrap-admin")
                )
            finally:
                repo.close()


class OwnershipApiTests(unittest.TestCase):
    def test_two_users_cannot_read_or_modify_each_others_strategy(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "ownership-api.sqlite3"
            identities = SQLiteAuthRepository(db_path)
            identities.create_user("admin@example.com", role=Role.ADMIN)
            identities.create_user("reader@example.com", role=Role.RESEARCHER)
            settings = LiveSettings(
                mode="mock",
                db_path=str(db_path),
                replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
                replay_speed=1000,
                heartbeat_seconds=0.05,
                access_mode="cloudflare",
                cloudflare_access_team_domain="team.cloudflareaccess.com",
                cloudflare_access_audience="audience",
                authorization_mode="enforced",
                bootstrap_admin_emails=("admin@example.com",),
            )
            market_repo = SQLiteBarRepository(db_path)
            market_repo.save(market_bar())
            app = create_app(
                settings,
                feed=ReplayFeed(settings.replay_csv, speed=1000, loop=False),
                repository=market_repo,
                access_validator=None,
                auth_repository=identities,
            )
            admin = {
                "X-Authenticated-Subject": "cf-admin",
                "X-Authenticated-Email": "admin@example.com",
            }
            reader = {
                "X-Authenticated-Subject": "cf-reader",
                "X-Authenticated-Email": "reader@example.com",
            }
            with TestClient(app) as client:
                created = client.post(
                    "/api/composite-strategies",
                    headers=reader,
                    json={"definition": default_composite_definition()},
                )
                self.assertEqual(created.status_code, 201, created.text)
                strategy_id = created.json()["id"]

                self.assertEqual(
                    len(client.get(
                        "/api/composite-strategies", headers=reader
                    ).json()["strategies"]),
                    1,
                )
                self.assertEqual(
                    client.get(
                        "/api/composite-strategies", headers=admin
                    ).json()["strategies"],
                    [],
                )
                self.assertEqual(
                    client.get(
                        f"/api/composite-strategies/{strategy_id}",
                        headers=admin,
                    ).status_code,
                    404,
                )
                cross_owner_definition = default_composite_definition()
                cross_owner_definition["name"] = "跨帳號引用"
                cross_owner_definition["entry"] = {
                    "operator": "all",
                    "confirmation_window_minutes": 15,
                    "rules": [{
                        "source": "composite",
                        "strategy_id": strategy_id,
                        "version": 1,
                    }],
                }
                cross_owner = client.post(
                    "/api/composite-strategies",
                    headers=admin,
                    json={"definition": cross_owner_definition},
                )
                self.assertEqual(cross_owner.status_code, 422)
                self.assertIn("找不到被引用", cross_owner.json()["detail"])
                self.assertEqual(
                    client.delete(
                        f"/api/composite-strategies/{strategy_id}",
                        headers=admin,
                    ).status_code,
                    404,
                )
                run = client.post(
                    "/api/backtest-runs",
                    headers=reader,
                    json={
                        "strategy": f"composite:{strategy_id}",
                        "start": "2026-08-25",
                        "end": "2026-08-25",
                    },
                )
                self.assertEqual(run.status_code, 201, run.text)
                run_id = run.json()["history_run_id"]
                self.assertEqual(
                    len(client.get(
                        "/api/backtest-runs", headers=reader
                    ).json()["runs"]),
                    1,
                )
                self.assertEqual(
                    client.get(
                        "/api/backtest-runs", headers=admin
                    ).json()["runs"],
                    [],
                )
                self.assertEqual(
                    client.get(
                        f"/api/backtest-runs/{run_id}", headers=admin
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    client.delete(
                        f"/api/backtest-runs/{run_id}", headers=admin
                    ).status_code,
                    404,
                )


if __name__ == "__main__":
    unittest.main()
