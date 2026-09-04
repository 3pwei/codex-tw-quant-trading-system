from datetime import date, datetime, timedelta
from pathlib import Path
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

                deleted = client.delete(f"/api/backtest-runs/{run_id}")
                self.assertEqual(deleted.status_code, 200, deleted.text)
                self.assertEqual(deleted.json()["deleted_run_id"], run_id)
                self.assertFalse(deleted.json()["released_strategy_reference"])
                self.assertEqual(client.get("/api/backtest-runs").json()["runs"], [])
                self.assertEqual(
                    client.get(f"/api/backtest-runs/{run_id}").status_code, 404
                )
                self.assertEqual(
                    client.delete(f"/api/backtest-runs/{run_id}").status_code, 404
                )

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
                self.assertNotIn("strategy_id", columns)
            finally:
                repo.close()


if __name__ == "__main__":
    unittest.main()
