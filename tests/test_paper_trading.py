from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from tw_quant.auth import (
    AccessIdentity,
    AccessTokenError,
    Role,
    SQLiteAuthRepository,
    TradingMode,
)
from tw_quant.live.api import create_app
from tw_quant.live.feed import ReplayFeed
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import SQLiteBarRepository
from tw_quant.market import KBar, TAIPEI


ROOT = Path(__file__).resolve().parents[1]


class PaperAccessValidator:
    def authenticate(self, token):
        identities = {
            "trader-token": AccessIdentity("cf-trader", "trader@example.com"),
            "other-token": AccessIdentity("cf-other", "other@example.com"),
            "reader-token": AccessIdentity("cf-reader", "reader@example.com"),
        }
        try:
            return identities[token]
        except KeyError as exc:
            raise AccessTokenError("invalid assertion") from exc


class PaperTradingApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "paper.sqlite3"
        auth = SQLiteAuthRepository(self.db_path)
        auth.create_user(
            "trader@example.com", role=Role.TRADER,
            trading_mode=TradingMode.PAPER,
        )
        auth.create_user(
            "other@example.com", role=Role.TRADER,
            trading_mode=TradingMode.PAPER,
        )
        auth.create_user("reader@example.com", role=Role.RESEARCHER)
        self.repository = SQLiteBarRepository(self.db_path)
        at = datetime.now(TAIPEI) - timedelta(minutes=1)
        self.repository.save(KBar(
            symbol="TMF", contract="TMFTEST", time=at.replace(second=0, microsecond=0),
            open=20_000, high=20_010, low=19_990, close=20_000, volume=100,
            status="forming", session="day", trading_date=at.date(),
            first_tick_time=at, last_tick_time=at, exchange_time=at,
            received_time=at, latency_ms=0,
        ))
        settings = LiveSettings(
            mode="mock", db_path=str(self.db_path),
            replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"), replay_speed=1000,
            heartbeat_seconds=0.05, access_mode="cloudflare",
            cloudflare_access_team_domain="team.cloudflareaccess.com",
            cloudflare_access_audience="audience", authorization_mode="enforced",
            bootstrap_admin_emails=("admin@example.com",),
        )
        self.client = TestClient(create_app(
            settings,
            feed=ReplayFeed(settings.replay_csv, speed=1000, loop=False),
            repository=self.repository,
            access_validator=PaperAccessValidator(),
            auth_repository=auth,
        ))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def headers(subject: str, email: str, key: str | None = None):
        result = {
            "X-Authenticated-Subject": subject,
            "X-Authenticated-Email": email,
        }
        if key:
            result["Idempotency-Key"] = key
        return result

    def test_market_order_is_risk_checked_filled_and_idempotent(self):
        headers = self.headers("cf-trader", "trader@example.com", "mobile-tap-1")
        payload = {
            "side": "buy", "quantity": 1, "stop_loss_price": 19_950,
        }
        first = self.client.post("/api/paper/orders", headers=headers, json=payload)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertTrue(first.json()["created"])
        order = first.json()["order"]
        self.assertEqual(order["status"], "filled")
        self.assertEqual(order["reference_price"], 20_000)

        repeated = self.client.post("/api/paper/orders", headers=headers, json=payload)
        self.assertEqual(repeated.status_code, 201, repeated.text)
        self.assertFalse(repeated.json()["created"])
        self.assertEqual(repeated.json()["order"]["order_id"], order["order_id"])

        account = self.client.get(
            "/api/paper/account",
            headers=self.headers("cf-trader", "trader@example.com"),
        ).json()
        self.assertEqual(account["account"]["open_contracts"], 1)
        self.assertEqual(account["positions"][0]["quantity"], 1)
        self.assertEqual(len(self.client.get(
            "/api/paper/fills",
            headers=self.headers("cf-trader", "trader@example.com"),
        ).json()["fills"]), 1)

    def test_owner_data_is_isolated_and_researcher_is_denied(self):
        created = self.client.post(
            "/api/paper/orders",
            headers=self.headers("cf-trader", "trader@example.com", "private-order"),
            json={"side": "sell", "stop_loss_price": 20_050},
        )
        self.assertEqual(created.status_code, 201)
        other = self.client.get(
            "/api/paper/orders",
            headers=self.headers("cf-other", "other@example.com"),
        )
        self.assertEqual(other.status_code, 200)
        self.assertEqual(other.json()["orders"], [])
        denied = self.client.get(
            "/api/paper/account",
            headers=self.headers("cf-reader", "reader@example.com"),
        )
        self.assertEqual(denied.status_code, 403)

    def test_kill_switch_blocks_new_exposure(self):
        identity = self.headers("cf-trader", "trader@example.com")
        activated = self.client.post(
            "/api/paper/kill-switch", headers=identity,
            json={"reason": "operator_test"},
        )
        self.assertEqual(activated.status_code, 200)
        self.assertTrue(activated.json()["kill_switch_active"])
        blocked = self.client.post(
            "/api/paper/orders",
            headers={**identity, "Idempotency-Key": "blocked-order"},
            json={"side": "buy", "stop_loss_price": 19_950},
        )
        self.assertEqual(blocked.status_code, 201)
        self.assertEqual(blocked.json()["order"]["status"], "rejected")
        self.assertEqual(
            blocked.json()["order"]["status_reason"], "kill_switch_active"
        )

        reset = self.client.post(
            "/api/paper/kill-switch/reset", headers=identity,
            json={"reason": "operator_resume"},
        )
        self.assertEqual(reset.status_code, 200)
        self.assertFalse(reset.json()["kill_switch_active"])

    def test_stale_market_price_cannot_be_used_for_a_paper_fill(self):
        old = datetime.now(TAIPEI) - timedelta(minutes=5)
        self.repository.save(KBar(
            symbol="TMF", contract="TMFSTALE", time=datetime.now(TAIPEI),
            open=19_000, high=19_010, low=18_990, close=19_000, volume=1,
            status="forming", session="day", trading_date=old.date(),
            first_tick_time=old, last_tick_time=old, exchange_time=old,
            received_time=old, latency_ms=0,
        ))
        response = self.client.post(
            "/api/paper/orders",
            headers=self.headers("cf-trader", "trader@example.com", "stale-order"),
            json={"side": "buy", "stop_loss_price": 18_950},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "market price is stale")


if __name__ == "__main__":
    unittest.main()
