from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from tw_quant.broker import (
    BrokerAccountRef,
    BrokerConnectionSettings,
    BrokerOrderRequest,
    BrokerSecretMaterial,
    CompositeOrderAdmissionGate,
    ExecutionMode,
    LockedOrderAdmissionGate,
    RecoveryStatus,
    SQLiteRecoveryLockRepository,
)
from tw_quant.execution_service import build_execution_service
from tw_quant.execution_service.health import (
    BrokerConnectionHealth,
    ExecutionServiceHealth,
)
from tw_quant.execution_service.redaction import SecretRedactionFilter
from tw_quant.market_data.settings import MarketDataSettings
from tw_quant.paper import SQLitePaperRepository


ROOT = Path(__file__).resolve().parents[1]
CONFIRMATION = "I_UNDERSTAND_LIVE_ORDERS"


class CountingGate:
    def __init__(self, *, reject: bool = False):
        self.calls = 0
        self.reject = reject

    def assert_ordering_allowed(self) -> None:
        self.calls += 1
        if self.reject:
            raise RuntimeError("rejected")


class ExecutionSecurityBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ca = self.root / "broker-ca.pfx"
        self.ca.write_bytes(b"test-certificate-placeholder")
        self.ca.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def environment(self, **changes: str) -> dict[str, str]:
        values = {
            "BROKER_PROVIDER": "shioaji",
            "LIVE_TRADING_ENABLED": "true",
            "LIVE_TRADING_CONFIRMATION": CONFIRMATION,
            "LIVE_BROKER_ACCOUNT_ID": "account-1234",
            "LIVE_ALLOWED_ACCOUNT_IDS": "account-1234",
            "LIVE_EXECUTION_DB_PATH": str(self.root / "live.sqlite3"),
            "LIVE_EXECUTION_HEALTH_PATH": str(self.root / "health.json"),
            "SJ_API_KEY": "api-test-value",
            "SJ_SECRET_KEY": "secret-test-value",
            "SJ_CA_CERT_PATH": str(self.ca),
            "SJ_CA_PASSWORD": "ca-test-value",
        }
        values.update(changes)
        return values

    async def test_execution_composition_root_starts_locked(self):
        runtime = build_execution_service(env=self.environment())
        try:
            await runtime.start()
            health = runtime.public_health()
            self.assertTrue(health["enabled"])
            self.assertTrue(health["locked"])
            self.assertEqual(health["recovery_status"], "locked")
            self.assertEqual(health["broker_name"], "shioaji")
            self.assertEqual(health["execution_state"], "locked")
            self.assertEqual(runtime.worker.snapshot()["dispatches"], 0)
        finally:
            await runtime.close()

    async def test_default_configuration_remains_disabled(self):
        runtime = build_execution_service(
            env={
                "LIVE_EXECUTION_DB_PATH": str(self.root / "disabled.sqlite3"),
                "LIVE_EXECUTION_HEALTH_PATH": str(
                    self.root / "disabled-health.json"
                ),
            }
        )
        try:
            health = runtime.public_health()
            self.assertEqual(health["broker_name"], "disabled")
            self.assertEqual(health["execution_state"], "disabled")
            self.assertTrue(health["locked"])
            self.assertIn("execution_disabled", runtime.issues)
            self.assertEqual(runtime.worker.snapshot()["dispatches"], 0)
        finally:
            await runtime.close()

    async def test_every_invalid_configuration_is_locked(self):
        cases = {
            "missing_api_key": {"SJ_API_KEY": ""},
            "missing_secret_key": {"SJ_SECRET_KEY": ""},
            "missing_account_id": {"LIVE_BROKER_ACCOUNT_ID": ""},
            "ca_certificate_unavailable": {
                "SJ_CA_CERT_PATH": str(self.root / "missing.pfx")
            },
            "invalid_live_trading_confirmation": {
                "LIVE_TRADING_CONFIRMATION": "wrong"
            },
            "account_not_allowlisted": {
                "LIVE_ALLOWED_ACCOUNT_IDS": "another"
            },
            "unknown_broker_provider": {"BROKER_PROVIDER": "unknown"},
        }
        for expected_issue, changes in cases.items():
            with self.subTest(case=expected_issue):
                runtime = build_execution_service(env=self.environment(**changes))
                try:
                    self.assertTrue(runtime.locked)
                    self.assertIn(expected_issue, runtime.issues)
                    self.assertEqual(runtime.worker.snapshot()["dispatches"], 0)
                finally:
                    await runtime.close()

    async def test_open_ca_permissions_fail_closed(self):
        self.ca.chmod(0o644)
        runtime = build_execution_service(env=self.environment())
        try:
            self.assertIn("ca_certificate_permissions_too_open", runtime.issues)
            self.assertIsNone(runtime.manager)
        finally:
            await runtime.close()

    async def test_first_revision_ca_environment_names_remain_compatible(self):
        env = self.environment()
        env["CA_CERT_PATH"] = env.pop("SJ_CA_CERT_PATH")
        env["CA_PASSWORD"] = env.pop("SJ_CA_PASSWORD")
        runtime = build_execution_service(env=env)
        try:
            self.assertIsNotNone(runtime.manager)
            self.assertNotIn("missing_ca_certificate", runtime.issues)
            self.assertNotIn("missing_ca_password", runtime.issues)
            self.assertEqual(runtime.worker.snapshot()["dispatches"], 0)
        finally:
            await runtime.close()

    async def test_even_complete_configuration_has_no_submit_path(self):
        runtime = build_execution_service(env=self.environment())
        try:
            self.assertIsNotNone(runtime.manager)
            self.assertIsNotNone(runtime.recovery_repository)
            self.assertEqual(runtime.manager.broker.broker_name, "disabled")
            attempt = runtime.recovery_repository.begin(
                "shioaji", "account-1234", updated_at=datetime.now(timezone.utc)
            )
            runtime.recovery_repository.complete(
                "shioaji",
                "account-1234",
                (),
                expected_generation=attempt.generation,
                updated_at=datetime.now(timezone.utc),
            )
            request = BrokerOrderRequest(
                client_order_id="never-submitted",
                owner_id="owner",
                strategy_id="strategy",
                strategy_version=1,
                symbol="TMF",
                contract="TMFR1",
                side="buy",
                quantity=1,
                mode=ExecutionMode.LIVE,
            )
            with self.assertRaisesRegex(RuntimeError, "not implemented"):
                runtime.manager.create(request)
            self.assertEqual(runtime.order_repository.orders(), [])
            self.assertEqual(runtime.worker.snapshot()["dispatches"], 0)
        finally:
            await runtime.close()

    async def test_paper_and_live_persistence_are_separate(self):
        runtime = build_execution_service(env=self.environment())
        paper = SQLitePaperRepository(self.root / "live.sqlite3")
        try:
            with sqlite3.connect(self.root / "live.sqlite3") as connection:
                tables = {
                    row[0] for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            self.assertTrue({
                "live_orders", "live_order_outbox", "live_recovery_lock"
            }.issubset(tables))
            self.assertTrue({
                "paper_events", "paper_order_read_model",
                "paper_fill_read_model", "paper_position_read_model",
            }.issubset(tables))
        finally:
            paper.close()
            await runtime.close()

    async def test_health_masks_account_and_never_serializes_secrets(self):
        env = self.environment()
        runtime = build_execution_service(env=env)
        try:
            health = runtime.public_health()
            self.assertEqual(health["masked_account_id"], "****1234")
            self.assertEqual(
                set(health),
                {
                    "broker_name",
                    "masked_account_id",
                    "execution_state",
                    "enabled",
                    "locked",
                    "recovery_status",
                },
            )
            payload = json.dumps(health)
            for key in ("SJ_API_KEY", "SJ_SECRET_KEY", "CA_PASSWORD"):
                self.assertNotIn(key, payload)
            for value in (
                env["SJ_API_KEY"], env["SJ_SECRET_KEY"], env["SJ_CA_PASSWORD"],
                env["LIVE_BROKER_ACCOUNT_ID"], env["SJ_CA_CERT_PATH"],
            ):
                self.assertNotIn(value, payload)
        finally:
            await runtime.close()

    async def test_fake_broker_secret_lookup_is_isolated_by_full_identity(self):
        first = BrokerAccountRef("broker-a", "account-1")
        second = BrokerAccountRef("broker-b", "account-1")
        materials = {
            first: BrokerSecretMaterial((("token", "credential-a"),)),
            second: BrokerSecretMaterial((("token", "credential-b"),)),
        }

        class FakeSecretProvider:
            def load(self, connection: BrokerConnectionSettings) -> BrokerSecretMaterial:
                account = connection.account_ref
                if account is None or account not in materials:
                    raise AssertionError("credential lookup crossed connection identity")
                return materials[account]

        provider = FakeSecretProvider()
        first_material = provider.load(BrokerConnectionSettings(
            "connection-a", "broker-a", "account-1", True, "fake:a"
        ))
        second_material = provider.load(BrokerConnectionSettings(
            "connection-b", "broker-b", "account-1", True, "fake:b"
        ))
        self.assertNotEqual(first, second)
        self.assertEqual(first_material.values[0][1], "credential-a")
        self.assertEqual(second_material.values[0][1], "credential-b")

    async def test_recovery_is_isolated_by_broker_and_account(self):
        repository = SQLiteRecoveryLockRepository(self.root / "recovery.sqlite3")
        now = datetime.now(timezone.utc)
        try:
            attempt = repository.begin("broker-a", "account-1", updated_at=now)
            repository.complete(
                "broker-a", "account-1", (),
                expected_generation=attempt.generation, updated_at=now,
            )
            first = repository.state("broker-a", "account-1")
            second = repository.state("broker-b", "account-1")
            self.assertEqual(first.status, RecoveryStatus.READY)
            self.assertEqual(second.status, RecoveryStatus.LOCKED)
            self.assertEqual(first.account_id, second.account_id)
            self.assertNotEqual(first.broker_name, second.broker_name)
        finally:
            repository.close()

    async def test_health_collection_represents_same_account_at_two_brokers(self):
        health = ExecutionServiceHealth((
            BrokerConnectionHealth(
                "connection-a", "broker-a",
                BrokerAccountRef("broker-a", "account-0001"),
                "ready", True, False, "ready",
            ),
            BrokerConnectionHealth(
                "connection-b", "broker-b",
                BrokerAccountRef("broker-b", "account-0001"),
                "locked", True, True, "locked",
            ),
        )).to_public_dict()
        connections = health["connections"]
        self.assertEqual(len(connections), 2)
        self.assertEqual(connections[0]["broker_name"], "broker-a")
        self.assertEqual(connections[1]["broker_name"], "broker-b")
        self.assertEqual(connections[0]["masked_account_id"], "****0001")
        self.assertEqual(connections[1]["masked_account_id"], "****0001")

    async def test_operational_log_has_masked_broker_account_context(self):
        runtime = build_execution_service(env=self.environment())
        try:
            with self.assertLogs("tw_quant.execution_service", logging.INFO) as logs:
                task = asyncio.create_task(runtime.serve())
                await asyncio.sleep(0)
                await runtime.close()
                await task
            payload = " ".join(logs.output)
            self.assertIn("broker=shioaji", payload)
            self.assertIn("account=****1234", payload)
            self.assertNotIn("account-1234", payload)
        finally:
            await runtime.close()


class BoundaryArchitectureTests(unittest.TestCase):
    def test_composite_gate_stops_at_first_rejection(self):
        first = CountingGate(reject=True)
        second = CountingGate()
        gate = CompositeOrderAdmissionGate((first, second))
        with self.assertRaisesRegex(RuntimeError, "rejected"):
            gate.assert_ordering_allowed()
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)

    def test_locked_gate_always_rejects(self):
        with self.assertRaisesRegex(RuntimeError, "not implemented"):
            LockedOrderAdmissionGate().assert_ordering_allowed()

    def test_public_application_does_not_construct_production_execution(self):
        source = (ROOT / "tw_quant/live/api.py").read_text(encoding="utf-8")
        self.assertIn("DisabledExecutionWorker()", source)
        self.assertNotIn("ShioajiBrokerAdapter", source)
        self.assertNotIn("execution_service", source)
        self.assertNotIn("CA_CERT_PATH", source)

    def test_public_containers_do_not_receive_live_secrets_or_ca(self):
        compose = (ROOT / "deploy/lightsail/docker-compose.yml").read_text()
        market = compose.split("  market-api:", 1)[1].split(
            "\n  execution-worker:", 1
        )[0]
        gateway = compose.split("  gateway:", 1)[1].split("\nvolumes:", 1)[0]
        execution = compose.split("  execution-worker:", 1)[1].split(
            "\n  gateway:", 1
        )[0]
        for public_service in (market, gateway):
            self.assertNotIn("EXECUTION_ENV_FILE", public_service)
            self.assertNotIn("live-secrets", public_service)
        self.assertIn("EXECUTION_ENV_FILE", execution)
        self.assertIn("live-secrets", execution)
        self.assertNotIn("ports:", execution)
        self.assertNotIn("expose:", execution)
        market_env = (ROOT / "deploy/lightsail/market.env.example").read_text()
        for key in (
            "SJ_API_KEY", "SJ_SECRET_KEY", "SJ_CA_CERT_PATH", "SJ_CA_PASSWORD",
            "LIVE_BROKER_ACCOUNT_ID", "LIVE_ALLOWED_ACCOUNT_IDS",
        ):
            self.assertNotRegex(market_env, rf"(?m)^{key}=")

    def test_execution_process_name_is_broker_neutral(self):
        compose = (ROOT / "deploy/lightsail/docker-compose.yml").read_text()
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("execution-worker:", compose)
        self.assertIn("AS execution-worker", dockerfile)
        for broker_specific_name in (
            "shioaji-worker", "shioaji-execution-worker", "shioaji-live-worker"
        ):
            self.assertNotIn(broker_specific_name, compose)
            self.assertNotIn(broker_specific_name, dockerfile)

    def test_market_production_does_not_read_live_secret_names(self):
        with mock.patch.dict(
            "os.environ",
            {
                "PLATFORM_ENVIRONMENT": "production",
                "MARKET_DATA_PROVIDER": "shioaji",
                "SJ_API_KEY": "live-only-api-key",
                "SJ_SEC_KEY": "legacy-live-secret",
            },
            clear=True,
        ):
            settings = MarketDataSettings.from_env()
        self.assertIsNone(settings.shioaji_api_key)
        self.assertIsNone(settings.shioaji_secret_key)

    def test_secret_redaction_removes_values_from_logs(self):
        record = logging.LogRecord(
            "test", logging.INFO, __file__, 1,
            "login api-value secret-value /sensitive/ca.pfx account-1234", (), None,
        )
        redactor = SecretRedactionFilter((
            "api-value", "secret-value", "/sensitive/ca.pfx", "account-1234"
        ))
        self.assertTrue(redactor.filter(record))
        message = record.getMessage()
        for value in (
            "api-value", "secret-value", "/sensitive/ca.pfx", "account-1234"
        ):
            self.assertNotIn(value, message)

    def test_execution_service_core_never_imports_shioaji_sdk(self):
        for path in (ROOT / "tw_quant/execution_service").glob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("import shioaji", source, path.name)
            self.assertNotIn("from shioaji", source, path.name)
            self.assertNotIn("broker.shioaji", source, path.name)
            self.assertNotIn("SJ_API_KEY", source, path.name)
            self.assertNotIn("SJ_SECRET_KEY", source, path.name)


if __name__ == "__main__":
    unittest.main()
