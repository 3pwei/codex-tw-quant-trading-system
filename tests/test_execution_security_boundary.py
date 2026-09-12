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
    BrokerOrderRequest,
    CompositeOrderAdmissionGate,
    ExecutionMode,
    LockedOrderAdmissionGate,
)
from tw_quant.execution_service import build_execution_service
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
            "CA_CERT_PATH": str(self.ca),
            "CA_PASSWORD": "ca-test-value",
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
            self.assertEqual(runtime.worker.snapshot()["dispatches"], 0)
        finally:
            await runtime.close()

    async def test_every_invalid_configuration_is_locked(self):
        cases = {
            "missing_api_key": {"SJ_API_KEY": ""},
            "missing_secret_key": {"SJ_SECRET_KEY": ""},
            "missing_account_id": {"LIVE_BROKER_ACCOUNT_ID": ""},
            "ca_certificate_unavailable": {
                "CA_CERT_PATH": str(self.root / "missing.pfx")
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
            self.assertEqual(health["masked_account"], "****1234")
            self.assertEqual(
                set(health),
                {"enabled", "locked", "broker", "masked_account", "recovery_status"},
            )
            payload = json.dumps(health)
            for key in ("SJ_API_KEY", "SJ_SECRET_KEY", "CA_PASSWORD"):
                self.assertNotIn(key, payload)
            for value in (
                env["SJ_API_KEY"], env["SJ_SECRET_KEY"], env["CA_PASSWORD"],
                env["LIVE_BROKER_ACCOUNT_ID"], env["CA_CERT_PATH"],
            ):
                self.assertNotIn(value, payload)
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
            "SJ_API_KEY", "SJ_SECRET_KEY", "CA_CERT_PATH", "CA_PASSWORD",
            "LIVE_BROKER_ACCOUNT_ID", "LIVE_ALLOWED_ACCOUNT_IDS",
        ):
            self.assertNotRegex(market_env, rf"(?m)^{key}=")

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


if __name__ == "__main__":
    unittest.main()
