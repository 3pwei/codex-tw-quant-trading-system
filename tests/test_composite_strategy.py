from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from tw_quant.backtest import run_composite_backtest
from tw_quant.live.api import create_app
from tw_quant.live.feed import ReplayFeed
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import SQLiteBarRepository
from tw_quant.market import KBar
from tw_quant.strategy import (
    default_composite_definition,
    validate_composite_definition,
)


TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]


def make_bar(minute: int, close: float, volume: int = 100) -> KBar:
    timestamp = datetime(2026, 8, 24, 15, 0, tzinfo=TAIPEI) + timedelta(minutes=minute)
    return KBar(
        symbol="TMF", contract="TMFU6", time=timestamp, open=close,
        high=close + 0.2, low=close - 0.2, close=close, volume=volume,
        status="closed", session="night", trading_date=date(2026, 8, 25),
        first_tick_time=timestamp, last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=10),
        latency_ms=10,
    )


def orb_composite() -> dict[str, object]:
    return {
        "name": "ORB 低週期進場",
        "description": "測試用組合",
        "enabled": True,
        "direction": "both",
        "setup": {
            "operator": "all", "confirmation_window_minutes": 15, "rules": [],
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
            "operator": "any", "confirmation_window_minutes": 5, "rules": [],
        },
        "risk": {
            "monitor_interval": "1m",
            "stop_loss_pct": 0.01,
            "take_profit_pct": 0.03,
            "max_holding_minutes": 120,
        },
    }


def minimal_backtest_result(strategy_id: str) -> dict[str, object]:
    return {
        "metadata": {
            "symbol": "TMF", "strategy": "組合測試",
            "strategy_key": strategy_id, "strategy_version": 1,
            "interval": "多週期", "interval_key": "multi",
            "date_range": "2026-08-25 ～ 2026-08-25",
        },
        "config": {}, "summary": {"net_profit": 0},
        "trades": [], "equity": [], "bars": [],
    }


class CompositeDefinitionTests(unittest.TestCase):
    def test_risk_is_required_and_entry_must_have_rule(self):
        value = default_composite_definition()
        del value["risk"]
        with self.assertRaisesRegex(ValueError, "risk"):
            validate_composite_definition(value)
        value = default_composite_definition()
        value["entry"] = {
            "operator": "all", "confirmation_window_minutes": 5, "rules": []
        }
        with self.assertRaisesRegex(ValueError, "entry"):
            validate_composite_definition(value)

    def test_composite_uses_atomic_signal_then_next_1m_open(self):
        bars = [make_bar(0, 100), make_bar(1, 100)]
        bars += [make_bar(2, 102, 500), make_bar(3, 103), make_bar(4, 104)]
        definition = validate_composite_definition(orb_composite())
        result = run_composite_backtest(
            bars, definition, "combo-1", 3,
            date(2026, 8, 25), date(2026, 8, 25),
        )
        self.assertEqual(result["metadata"]["strategy_version"], 3)
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["entry_time"], bars[4].time.isoformat(timespec="milliseconds"))
        self.assertEqual(result["trades"][0]["stop_loss_price"], 102.96)
        self.assertTrue(result["trace"])

    def test_repository_keeps_immutable_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "bars.sqlite3")
            try:
                definition = validate_composite_definition(orb_composite())
                first = repo.save_composite_strategy("combo", definition)
                changed = {**definition, "name": "新版"}
                second = repo.save_composite_strategy("combo", changed)
                self.assertEqual((first["version"], second["version"]), (1, 2))
                self.assertEqual(
                    [item["version"] for item in repo.composite_strategy_versions("combo")],
                    [2, 1],
                )
                self.assertEqual(repo.composite_strategy("combo", 1)["name"], "ORB 低週期進場")
                self.assertEqual(repo.composite_strategies()[0]["name"], "新版")
                archived = repo.archive_composite_strategy("combo")
                self.assertEqual(archived["id"], "combo")
                self.assertTrue(repo.composite_strategy_archived("combo"))
                self.assertEqual(repo.composite_strategies(), [])
                self.assertEqual(repo.archived_composite_strategies()[0]["version"], 2)
                self.assertIn("archived_at", repo.archived_composite_strategies()[0])
                self.assertEqual(repo.composite_strategy("combo", 1)["name"], "ORB 低週期進場")
                with self.assertRaisesRegex(ValueError, "不可修改"):
                    repo.save_composite_strategy("combo", changed)
            finally:
                repo.close()

    def test_permanent_delete_requires_archive_and_rejects_references_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "bars.sqlite3")
            try:
                definition = validate_composite_definition(orb_composite())
                repo.save_composite_strategy("referenced", definition)
                repo.save_composite_strategy("unused", definition)
                with self.assertRaisesRegex(ValueError, "只有封存策略"):
                    repo.purge_archived_composite_strategies(["unused"])
                repo.archive_composite_strategy("referenced")
                repo.archive_composite_strategy("unused")
                saved_run = repo.save_backtest_run(
                    minimal_backtest_result("referenced"), "composite",
                    "referenced", 1, definition,
                )
                with self.assertRaisesRegex(ValueError, "回測引用"):
                    repo.purge_archived_composite_strategies(
                        ["referenced", "unused"]
                    )
                self.assertIsNotNone(repo.composite_strategy("unused", 1))
                deleted = repo.delete_backtest_run(saved_run["run_id"])
                self.assertTrue(deleted["released_strategy_reference"])
                self.assertEqual(deleted["strategy_key"], "referenced")
                result = repo.purge_archived_composite_strategies(
                    ["referenced", "unused"]
                )
                self.assertEqual(result["deleted_strategies"], 2)
                self.assertEqual(result["deleted_versions"], 2)
                self.assertIsNone(repo.composite_strategy("unused", 1))
            finally:
                repo.close()


class CompositeApiTests(unittest.TestCase):
    def test_crud_versions_and_backtest_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.sqlite3"
            repo = SQLiteBarRepository(path)
            for bar in [make_bar(0, 100), make_bar(1, 100), make_bar(2, 102, 500), make_bar(3, 103), make_bar(4, 104)]:
                repo.save(bar)
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
                created = client.post(
                    "/api/composite-strategies", json={"definition": orb_composite()}
                )
                self.assertEqual(created.status_code, 201, created.text)
                item = created.json()
                self.assertEqual(item["version"], 1)
                updated_definition = orb_composite()
                updated_definition["name"] = "ORB v2"
                updated = client.put(
                    f"/api/composite-strategies/{item['id']}",
                    json={"definition": updated_definition},
                )
                self.assertEqual(updated.json()["version"], 2)
                listing = client.get("/api/composite-strategies").json()
                self.assertEqual(listing["strategies"][0]["version"], 2)
                self.assertEqual(listing["archived_strategies"], [])
                versions = client.get(
                    f"/api/composite-strategies/{item['id']}/versions"
                )
                self.assertEqual(versions.status_code, 200, versions.text)
                self.assertEqual(
                    [version["version"] for version in versions.json()["versions"]],
                    [2, 1],
                )
                self.assertFalse(versions.json()["archived"])
                result = client.get(
                    "/api/composite-backtest",
                    params={
                        "strategy_id": item["id"], "version": 1,
                        "start": "2026-08-25", "end": "2026-08-25",
                    },
                )
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["metadata"]["strategy_version"], 1)
                archived = client.delete(
                    f"/api/composite-strategies/{item['id']}"
                )
                self.assertEqual(archived.status_code, 200, archived.text)
                self.assertIn("archived_at", archived.json())
                self.assertEqual(
                    client.get("/api/composite-strategies").json()["strategies"],
                    [],
                )
                archived_listing = client.get("/api/composite-strategies").json()
                self.assertEqual(
                    archived_listing["archived_strategies"][0]["id"], item["id"]
                )
                archived_versions = client.get(
                    f"/api/composite-strategies/{item['id']}/versions"
                ).json()
                self.assertTrue(archived_versions["archived"])
                old_version = client.get(
                    f"/api/composite-strategies/{item['id']}?version=1"
                )
                self.assertEqual(old_version.status_code, 200)
                rejected_update = client.put(
                    f"/api/composite-strategies/{item['id']}",
                    json={"definition": updated_definition},
                )
                self.assertEqual(rejected_update.status_code, 410)
                options = client.get("/api/backtest/options?symbol=TMF").json()
                self.assertFalse(any(
                    option["key"] == f"composite:{item['id']}"
                    for option in options["strategies"]
                ))
                saved_history = client.post(
                    "/api/backtest-runs",
                    json={
                        "strategy": f"composite:{item['id']}",
                        "version": 1,
                        "start": "2026-08-25",
                        "end": "2026-08-25",
                    },
                )
                self.assertEqual(saved_history.status_code, 201, saved_history.text)
                run_id = saved_history.json()["history_run_id"]
                history = client.get("/api/backtest-runs").json()["runs"]
                self.assertEqual(history[0]["run_id"], run_id)
                self.assertEqual(history[0]["strategy_version"], 1)
                detail = client.get(f"/api/backtest-runs/{run_id}")
                self.assertEqual(detail.status_code, 200, detail.text)
                self.assertEqual(detail.json()["strategy_snapshot"]["name"], "ORB 低週期進場")
                blocked = client.post(
                    "/api/composite-strategies/purge",
                    json={"strategy_ids": [item["id"]]},
                )
                self.assertEqual(blocked.status_code, 409, blocked.text)
                self.assertIn("回測引用", blocked.json()["detail"])
                deleted_run = client.delete(f"/api/backtest-runs/{run_id}")
                self.assertEqual(deleted_run.status_code, 200, deleted_run.text)
                self.assertTrue(
                    deleted_run.json()["released_strategy_reference"]
                )
                purged = client.post(
                    "/api/composite-strategies/purge",
                    json={"strategy_ids": [item["id"]]},
                )
                self.assertEqual(purged.status_code, 200, purged.text)
                self.assertEqual(purged.json()["deleted_versions"], 2)
                self.assertEqual(
                    client.get(
                        f"/api/composite-strategies/{item['id']}/versions"
                    ).status_code,
                    404,
                )


if __name__ == "__main__":
    unittest.main()
