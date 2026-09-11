from datetime import date, datetime, timedelta
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tw_quant.live.api import create_app
from tw_quant.live.feed import ReplayFeed
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import SQLiteBarRepository
from tw_quant.market import KBar


ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")


def bar(minute: int, close: float) -> KBar:
    timestamp = datetime(2026, 8, 24, 15, 0, tzinfo=TAIPEI) + timedelta(
        minutes=minute
    )
    return KBar(
        symbol="TMF", contract="TMFU6", time=timestamp, open=close,
        high=close + 1, low=close - 1, close=close, volume=100,
        status="closed", session="night", trading_date=date(2026, 8, 25),
        first_tick_time=timestamp,
        last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=10),
        latency_ms=10,
    )


class BacktestHistoryTests(unittest.TestCase):
    def test_atomic_backtest_is_saved_and_queryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            repo = SQLiteBarRepository(path)
            for minute, close in enumerate([100, 100, 102, 103, 101, 99]):
                repo.save(bar(minute, close))
            settings = LiveSettings(
                mode="mock", db_path=str(path),
                replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
                replay_speed=1000, heartbeat_seconds=0.05,
            )
            app = create_app(
                settings,
                feed=ReplayFeed(settings.replay_csv, speed=1000, loop=False),
                repository=repo,
            )
            with TestClient(app) as client:
                response = client.post(
                    "/api/backtest-runs",
                    json={
                        "strategy": "orb", "interval": "1m",
                        "start": "2026-08-25", "end": "2026-08-25",
                    },
                )
                self.assertEqual(response.status_code, 201, response.text)
                run_id = response.json()["history_run_id"]
                listing = client.get("/api/backtest-runs").json()["runs"]
                self.assertEqual(listing[0]["run_id"], run_id)
                self.assertEqual(listing[0]["strategy_kind"], "atomic")
                self.assertIsNone(listing[0]["strategy_version"])
                detail = client.get(f"/api/backtest-runs/{run_id}").json()
                self.assertEqual(detail["strategy_snapshot"]["opening_range_minutes"], 15)
                self.assertNotIn("bars", detail["result"])

                second = client.post(
                    "/api/backtest-runs",
                    json={
                        "strategy": "orb", "interval": "1m",
                        "start": "2026-08-25", "end": "2026-08-25",
                    },
                )
                self.assertEqual(second.status_code, 201, second.text)
                first_page = client.get("/api/backtest-runs?limit=1").json()
                second_page = client.get(
                    "/api/backtest-runs?limit=1&offset=1"
                ).json()
                self.assertTrue(first_page["has_more"])
                self.assertFalse(second_page["has_more"])
                self.assertNotEqual(
                    first_page["runs"][0]["run_id"],
                    second_page["runs"][0]["run_id"],
                )

                # List queries use denormalized summaries and never need to
                # load or parse the potentially large result payload.
                with repo.lock:
                    repo.connection.execute(
                        "UPDATE backtest_runs SET result_json='not-list-json' "
                        "WHERE run_id=?",
                        (run_id,),
                    )
                    repo.connection.commit()
                lightweight = client.get("/api/backtest-runs").json()["runs"]
                lightweight_run = next(
                    item for item in lightweight if item["run_id"] == run_id
                )
                self.assertEqual(lightweight_run["summary"], listing[0]["summary"])
                self.assertEqual(
                    lightweight_run["trade_count"], listing[0]["trade_count"]
                )

                deleted = client.delete(f"/api/backtest-runs/{run_id}")
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertEqual(deleted.json()["deleted_run_id"], run_id)
                self.assertFalse(deleted.json()["released_strategy_reference"])
                remaining = client.get("/api/backtest-runs").json()["runs"]
                self.assertEqual(len(remaining), 1)
                self.assertEqual(
                    client.get(f"/api/backtest-runs/{run_id}").status_code, 404
                )
                self.assertEqual(
                    client.delete(f"/api/backtest-runs/{run_id}").status_code, 404
                )

    def test_batch_delete_is_atomic_and_can_delete_all_owned_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "market.sqlite3"
            repo = SQLiteBarRepository(path)
            for minute, close in enumerate([100, 100, 102, 103, 101, 99]):
                repo.save(bar(minute, close))
            settings = LiveSettings(
                mode="mock", db_path=str(path),
                replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
                replay_speed=1000, heartbeat_seconds=0.05,
            )
            app = create_app(
                settings,
                feed=ReplayFeed(settings.replay_csv, speed=1000, loop=False),
                repository=repo,
            )
            payload = {
                "strategy": "orb", "interval": "1m",
                "start": "2026-08-25", "end": "2026-08-25",
            }
            with TestClient(app) as client:
                run_ids = [
                    client.post("/api/backtest-runs", json=payload).json()[
                        "history_run_id"
                    ]
                    for _ in range(3)
                ]

                empty = client.request(
                    "DELETE", "/api/backtest-runs", json={"run_ids": []}
                )
                self.assertEqual(empty.status_code, 400, empty.text)

                deleted = client.request(
                    "DELETE", "/api/backtest-runs",
                    json={"run_ids": run_ids[:2]},
                )
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertEqual(deleted.json()["deleted_runs"], 2)
                self.assertEqual(
                    deleted.json()["released_strategy_references"], 0
                )

                missing = client.request(
                    "DELETE", "/api/backtest-runs",
                    json={"run_ids": [run_ids[2], "missing-run"]},
                )
                self.assertEqual(missing.status_code, 404, missing.text)
                self.assertEqual(
                    client.get(f"/api/backtest-runs/{run_ids[2]}").status_code,
                    200,
                )

                conflicting = client.request(
                    "DELETE", "/api/backtest-runs",
                    json={"run_ids": [run_ids[2]], "delete_all": True},
                )
                self.assertEqual(conflicting.status_code, 400, conflicting.text)

                deleted_all = client.request(
                    "DELETE", "/api/backtest-runs",
                    json={"delete_all": True},
                )
                self.assertEqual(deleted_all.status_code, 200, deleted_all.text)
                self.assertEqual(deleted_all.json()["deleted_runs"], 1)
                self.assertEqual(
                    client.get("/api/backtest-runs").json()["runs"], []
                )

    def test_batch_delete_never_crosses_owner_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "market.sqlite3")
            result = {
                "metadata": {
                    "strategy": "ORB", "symbol": "TMF", "interval": "1m",
                    "date_range": "2026-08-25 ～ 2026-08-25",
                },
                "summary": {},
                "trades": [],
            }
            try:
                owner_one = repo.save_backtest_run(
                    result, "atomic", "orb", None, {}, "owner-1"
                )
                owner_two = repo.save_backtest_run(
                    result, "atomic", "orb", None, {}, "owner-2"
                )

                rejected = repo.delete_backtest_runs(
                    [owner_one["run_id"], owner_two["run_id"]],
                    owner_user_id="owner-1",
                )
                self.assertIsNone(rejected)
                self.assertIsNotNone(
                    repo.backtest_run(owner_one["run_id"], "owner-1")
                )

                deleted = repo.delete_backtest_runs(
                    [], delete_all=True, owner_user_id="owner-1"
                )
                self.assertEqual(deleted["deleted_runs"], 1)
                self.assertIsNone(
                    repo.backtest_run(owner_one["run_id"], "owner-1")
                )
                self.assertIsNotNone(
                    repo.backtest_run(owner_two["run_id"], "owner-2")
                )
            finally:
                repo.close()

    def test_empty_legacy_reference_table_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    strategy_version INTEGER NOT NULL,
                    strategy_snapshot_json TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    parameters_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()
            repo = SQLiteBarRepository(path)
            try:
                columns = {
                    row["name"]
                    for row in repo.connection.execute(
                        "PRAGMA table_info(backtest_runs)"
                    ).fetchall()
                }
                self.assertIn("strategy_kind", columns)
                self.assertIn("result_json", columns)
                self.assertIn("summary_json", columns)
                self.assertIn("trade_count", columns)
                self.assertNotIn("strategy_id", columns)
            finally:
                repo.close()

    def test_existing_results_are_backfilled_for_lightweight_listing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current-schema.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(
                """
                CREATE TABLE backtest_runs (
                    run_id TEXT PRIMARY KEY,
                    strategy_kind TEXT NOT NULL,
                    strategy_key TEXT NOT NULL,
                    strategy_version INTEGER,
                    strategy_name TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    strategy_snapshot_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL
                )
                """
            )
            result = {
                "summary": {"net_profit": 123.0},
                "trades": [{"net_pnl": 123.0}],
                "equity": [],
            }
            connection.execute(
                "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "run-1", "atomic", "orb", None, "ORB", "TMF", "1m",
                    "2026-08-25", "2026-08-25", "{}",
                    json.dumps(result), "completed",
                    "2026-08-25T00:00:00+08:00", "owner-1",
                ),
            )
            connection.commit()
            connection.close()

            repo = SQLiteBarRepository(path)
            try:
                listing = repo.backtest_runs(50, 0, owner_user_id="owner-1")
                self.assertEqual(listing[0]["summary"]["net_profit"], 123.0)
                self.assertEqual(listing[0]["trade_count"], 1)
            finally:
                repo.close()

    def test_backtest_date_query_uses_covering_order_index(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "market.sqlite3")
            try:
                plan = repo.connection.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM minute_bars "
                    "WHERE symbol=? AND status='closed' "
                    "AND trading_date BETWEEN ? AND ? "
                    "ORDER BY trading_date, time",
                    ("TMF", "2026-08-01", "2026-08-31"),
                ).fetchall()
                details = " ".join(row["detail"] for row in plan)
                self.assertIn(
                    "idx_minute_bars_symbol_status_date_time", details
                )
                self.assertNotIn("USE TEMP B-TREE", details)
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
