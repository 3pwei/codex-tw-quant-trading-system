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
    generate_composite_signals,
    validate_composite_dependencies,
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


def referencing_composite(
    name: str, child_id: str, child_version: int = 1
) -> dict[str, object]:
    definition = orb_composite()
    definition["name"] = name
    definition["entry"] = {
        "operator": "all",
        "confirmation_window_minutes": 15,
        "rules": [{
            "source": "composite",
            "strategy_id": child_id,
            "version": child_version,
        }],
    }
    return definition


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

    def test_nested_composite_uses_pinned_child_entry_signal(self):
        bars = [make_bar(0, 100), make_bar(1, 100)]
        bars += [make_bar(2, 102, 500), make_bar(3, 103)]
        bars += [make_bar(4, 104), make_bar(5, 105), make_bar(6, 106)]
        child = validate_composite_definition(orb_composite())
        parent_raw = referencing_composite("父組合", "child", 1)
        parent = validate_composite_definition(
            parent_raw,
            composite_resolver=lambda strategy_id, version: {
                "id": strategy_id,
                "version": version,
                "name": child["name"],
                "definition": child,
            },
        )
        validate_composite_dependencies(parent, "parent")
        child_signals, _ = generate_composite_signals(bars, child)
        parent_signals, trace = generate_composite_signals(bars, parent)
        child_entry = next(
            item for item in child_signals if item["event"] == "entry"
        )
        parent_entry = next(
            item for item in parent_signals if item["event"] == "entry"
        )
        self.assertGreater(parent_entry["time"], child_entry["time"])
        self.assertTrue(trace)

    def test_dependency_validation_rejects_cycle_and_fourth_level(self):
        atomic = validate_composite_definition(orb_composite())

        def embedded(child_id: str, child: dict[str, object]):
            definition = referencing_composite("包裝", child_id)
            definition["entry"]["rules"][0].update({
                "name": child["name"], "definition": child,
            })
            return definition

        level_two = embedded("level-1", atomic)
        level_three = embedded("level-2", level_two)
        validate_composite_dependencies(level_three, "level-3")
        level_four = embedded("level-3", level_three)
        with self.assertRaisesRegex(ValueError, "最多支援 3 層"):
            validate_composite_dependencies(level_four, "level-4")

        cycle = embedded("child", atomic)
        cycle["entry"]["rules"][0]["definition"] = embedded("root", atomic)
        with self.assertRaisesRegex(ValueError, "循環引用"):
            validate_composite_dependencies(cycle, "root")

    def test_repository_keeps_immutable_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "bars.sqlite3")
            try:
                definition = validate_composite_definition(orb_composite())
                first = repo.save_composite_strategy("combo", definition)
                changed = {**definition, "description": "新版內容"}
                second = repo.save_composite_strategy("combo", changed)
                self.assertEqual((first["version"], second["version"]), (1, 2))
                self.assertEqual(
                    [item["version"] for item in repo.composite_strategy_versions("combo")],
                    [2, 1],
                )
                self.assertEqual(repo.composite_strategy("combo", 1)["name"], "ORB 低週期進場")
                self.assertEqual(
                    repo.composite_strategies()[0]["name"], "ORB 低週期進場"
                )
                renamed = {**definition, "name": "新版"}
                with self.assertRaisesRegex(ValueError, "另存為新策略"):
                    repo.save_composite_strategy("combo", renamed)
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
                unused_definition = {**definition, "name": "未引用策略"}
                repo.save_composite_strategy("unused", unused_definition)
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
                updated_definition["description"] = "第二版"
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

    def test_rename_starts_new_lineage_and_name_is_reserved_when_archived(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.sqlite3"
            settings = LiveSettings(
                mode="mock", db_path=str(path),
                replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
                replay_speed=1000, heartbeat_seconds=0.05,
            )
            app = create_app(settings)
            with TestClient(app) as client:
                created = client.post(
                    "/api/composite-strategies",
                    json={"definition": orb_composite()},
                ).json()
                renamed_definition = {**orb_composite(), "name": "全新名稱"}
                renamed_response = client.put(
                    f"/api/composite-strategies/{created['id']}",
                    json={"definition": renamed_definition},
                )
                self.assertEqual(renamed_response.status_code, 200)
                renamed = renamed_response.json()
                self.assertNotEqual(renamed["id"], created["id"])
                self.assertEqual(renamed["version"], 1)
                self.assertEqual(
                    renamed["created_from_strategy_id"], created["id"]
                )
                self.assertEqual(
                    {item["name"] for item in client.get(
                        "/api/composite-strategies"
                    ).json()["strategies"]},
                    {"ORB 低週期進場", "全新名稱"},
                )

                duplicate_active = client.post(
                    "/api/composite-strategies",
                    json={"definition": renamed_definition},
                )
                self.assertEqual(duplicate_active.status_code, 409)
                self.assertIn("已存在", duplicate_active.json()["detail"])

                archived = client.delete(
                    f"/api/composite-strategies/{renamed['id']}"
                )
                self.assertEqual(archived.status_code, 200)
                duplicate_archived = client.post(
                    "/api/composite-strategies",
                    json={"definition": renamed_definition},
                )
                self.assertEqual(duplicate_archived.status_code, 409)
                self.assertIn("包含封存策略", duplicate_archived.json()["detail"])

    def test_nested_versions_archive_rules_and_dependency_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested.sqlite3"
            settings = LiveSettings(
                mode="mock", db_path=str(path),
                replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
                replay_speed=1000, heartbeat_seconds=0.05,
            )
            app = create_app(settings)
            with TestClient(app) as client:
                child = client.post(
                    "/api/composite-strategies",
                    json={"definition": orb_composite()},
                ).json()
                parent_response = client.post(
                    "/api/composite-strategies",
                    json={"definition": referencing_composite(
                        "父組合", child["id"], child["version"]
                    )},
                )
                self.assertEqual(parent_response.status_code, 201)
                parent = parent_response.json()
                rule = parent["definition"]["entry"]["rules"][0]
                self.assertEqual(rule["strategy_id"], child["id"])
                self.assertEqual(rule["version"], 1)
                self.assertEqual(rule["definition"]["name"], child["name"])

                updated_child = orb_composite()
                updated_child["description"] = "child v2"
                child_v2 = client.put(
                    f"/api/composite-strategies/{child['id']}",
                    json={"definition": updated_child},
                ).json()
                self.assertEqual(child_v2["version"], 2)
                reference_options = client.get(
                    "/api/composite-strategies"
                ).json()["reference_strategies"]
                child_option = next(
                    item for item in reference_options
                    if item["id"] == child["id"]
                )
                self.assertEqual(
                    [item["version"] for item in child_option["versions"]],
                    [2, 1],
                )
                stored_parent = client.get(
                    f"/api/composite-strategies/{parent['id']}?version=1"
                ).json()
                self.assertEqual(
                    stored_parent["definition"]["entry"]["rules"][0]["version"],
                    1,
                )

                self.assertEqual(client.delete(
                    f"/api/composite-strategies/{child['id']}"
                ).status_code, 200)
                reference_options = client.get(
                    "/api/composite-strategies"
                ).json()["reference_strategies"]
                self.assertFalse(any(
                    item["id"] == child["id"] for item in reference_options
                ))
                archived_reference = client.post(
                    "/api/composite-strategies",
                    json={"definition": referencing_composite(
                        "不可建立", child["id"], 1
                    )},
                )
                self.assertEqual(archived_reference.status_code, 422)
                self.assertIn("封存策略不可加入", archived_reference.json()["detail"])

                blocked = client.post(
                    "/api/composite-strategies/purge",
                    json={"strategy_ids": [child["id"]]},
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertIn("其他組合策略引用", blocked.json()["detail"])

                self.assertEqual(client.delete(
                    f"/api/composite-strategies/{parent['id']}"
                ).status_code, 200)
                purged = client.post(
                    "/api/composite-strategies/purge",
                    json={"strategy_ids": [parent["id"], child["id"]]},
                )
                self.assertEqual(purged.status_code, 200, purged.text)
                self.assertEqual(purged.json()["deleted_strategies"], 2)

    def test_nested_api_rejects_cycle_and_more_than_three_levels(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = LiveSettings(
                mode="mock", db_path=str(Path(directory) / "depth.sqlite3"),
                replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
                replay_speed=1000, heartbeat_seconds=0.05,
            )
            app = create_app(settings)
            with TestClient(app) as client:
                first = client.post(
                    "/api/composite-strategies",
                    json={"definition": orb_composite()},
                ).json()
                second = client.post(
                    "/api/composite-strategies",
                    json={"definition": referencing_composite(
                        "第二層", first["id"]
                    )},
                ).json()
                third = client.post(
                    "/api/composite-strategies",
                    json={"definition": referencing_composite(
                        "第三層", second["id"]
                    )},
                ).json()
                too_deep = client.post(
                    "/api/composite-strategies",
                    json={"definition": referencing_composite(
                        "第四層", third["id"]
                    )},
                )
                self.assertEqual(too_deep.status_code, 422)
                self.assertIn("最多支援 3 層", too_deep.json()["detail"])

                cycle = client.put(
                    f"/api/composite-strategies/{first['id']}",
                    json={"definition": referencing_composite(
                        first["name"], third["id"]
                    )},
                )
                self.assertEqual(cycle.status_code, 422)
                self.assertIn("循環引用", cycle.json()["detail"])


if __name__ == "__main__":
    unittest.main()
