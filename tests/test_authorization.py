import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tw_quant.auth import (
    AccessIdentity,
    AccessTokenError,
    Role,
    SQLiteAuthRepository,
)
from tw_quant.live.api import create_app
from tw_quant.live.feed import ReplayFeed
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import SQLiteBarRepository

ROOT = Path(__file__).resolve().parents[1]


class FakeAccessValidator:
    def authenticate(self, token):
        identities = {
            "admin-token": AccessIdentity("cf-admin", "admin@example.com"),
            "reader-token": AccessIdentity("cf-reader", "reader@example.com"),
        }
        try:
            return identities[token]
        except KeyError as exc:
            raise AccessTokenError("invalid assertion") from exc


class AuthorizationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "authorization.sqlite3"
        identities = SQLiteAuthRepository(self.db_path)
        identities.create_user("admin@example.com", role=Role.ADMIN)
        identities.create_user("reader@example.com", role=Role.RESEARCHER)
        settings = LiveSettings(
            mode="mock",
            db_path=str(self.db_path),
            replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"),
            replay_speed=1000,
            heartbeat_seconds=0.05,
            access_mode="cloudflare",
            cloudflare_access_team_domain="team.cloudflareaccess.com",
            cloudflare_access_audience="audience",
            authorization_mode="enforced",
            bootstrap_admin_emails=("admin@example.com",),
        )
        self.app = create_app(
            settings,
            feed=ReplayFeed(settings.replay_csv, speed=1000, loop=False),
            repository=SQLiteBarRepository(self.db_path),
            access_validator=FakeAccessValidator(),
            auth_repository=identities,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    @staticmethod
    def headers(subject: str, email: str):
        return {
            "X-Authenticated-Subject": subject,
            "X-Authenticated-Email": email,
        }

    def test_role_blocks_admin_api_and_protected_static_route(self):
        reader = self.headers("cf-reader", "reader@example.com")
        denied = self.client.get("/api/admin/users", headers=reader)
        self.assertEqual(denied.status_code, 403)

        protected_page = self.client.get(
            "/internal/auth/cloudflare",
            headers={
                "Cf-Access-Jwt-Assertion": "reader-token",
                "X-Original-Uri": "/settings/",
            },
        )
        self.assertEqual(protected_page.status_code, 403)

        ordinary_page = self.client.get(
            "/internal/auth/cloudflare",
            headers={
                "Cf-Access-Jwt-Assertion": "reader-token",
                "X-Original-Uri": "/backtest/",
            },
        )
        self.assertEqual(ordinary_page.status_code, 204)

    def test_admin_can_create_and_change_user_with_audit(self):
        admin = self.headers("cf-admin", "admin@example.com")
        created = self.client.post(
            "/api/admin/users",
            headers=admin,
            json={
                "email": "trader@example.com",
                "role": "trader",
                "status": "active",
                "trading_mode": "paper",
            },
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["trading_mode"], "paper")

        listed = self.client.get("/api/admin/users", headers=admin)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["users"]), 3)

        audit = self.client.get("/api/admin/audit", headers=admin)
        self.assertEqual(audit.status_code, 200)
        self.assertIn(
            "user.created", [event["action"] for event in audit.json()["events"]]
        )

    def test_health_hides_provider_details_from_regular_users(self):
        reader = self.headers("cf-reader", "reader@example.com")
        public_health = self.client.get("/api/health", headers=reader)
        self.assertEqual(public_health.status_code, 200)
        self.assertNotIn("market_data_provider", public_health.json())
        self.assertNotIn("queue_size", public_health.json())

        admin_health = self.client.get(
            "/api/admin/health",
            headers=self.headers("cf-admin", "admin@example.com"),
        )
        self.assertEqual(admin_health.status_code, 200)
        self.assertIn("market_data_provider", admin_health.json())
        self.assertIn("queue_size", admin_health.json())

    def test_websocket_requires_market_permission_and_identity(self):
        with self.client.websocket_connect(
            "/ws/market/TMF",
            headers=self.headers("cf-reader", "reader@example.com"),
        ) as socket:
            status = socket.receive_json()
            self.assertEqual(status["type"], "status")
            self.assertNotIn("market_data_provider", status)

        with self.assertRaises(WebSocketDisconnect) as caught, \
                self.client.websocket_connect("/ws/market/TMF"):
            pass
        self.assertEqual(caught.exception.code, 1008)

    def test_unknown_api_route_fails_closed_before_routing(self):
        response = self.client.get(
            "/api/not-yet-classified",
            headers=self.headers("cf-reader", "reader@example.com"),
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
