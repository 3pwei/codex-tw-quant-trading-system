import asyncio
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from tw_quant.live.api import create_app
from tw_quant.live.access import AccessIdentity, AccessTokenError
from tw_quant.live.feed import ReplayFeed
from tw_quant.live.service import LiveMarketService
from tw_quant.live.settings import LiveSettings
from tw_quant.live.storage import SQLiteBarRepository
from tw_quant.live.models import TickEvent
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


class LiveApiTests(unittest.TestCase):
    def make_client(self):
        temp = tempfile.TemporaryDirectory()
        repository = SQLiteBarRepository(Path(temp.name) / "bars.sqlite3")
        settings = LiveSettings(
            mode="mock", db_path=str(Path(temp.name) / "bars.sqlite3"),
            replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"), replay_speed=1000,
            heartbeat_seconds=0.05,
        )
        feed = ReplayFeed(settings.replay_csv, speed=1000, loop=False)
        app = create_app(settings, feed=feed, repository=repository)
        return temp, TestClient(app)

    def test_rest_history_and_health(self):
        temp, client = self.make_client()
        try:
            with client:
                liveness = client.get("/health/live")
                self.assertEqual(liveness.status_code, 200)
                self.assertEqual(liveness.json(), {"status": "ok"})
                deadline = time.time() + 2
                bars = []
                while time.time() < deadline:
                    bars = client.get("/api/kbars?symbol=TMF&interval=1m&limit=500").json()
                    if len(bars) >= 3:
                        break
                    time.sleep(0.02)
                self.assertGreaterEqual(len(bars), 3)
                self.assertEqual(bars[-1]["type"], "kbar")
                self.assertIn(bars[-1]["status"], {"forming", "closed"})
                health = client.get("/api/health").json()
                self.assertEqual(health["symbol"], "TMF")
                self.assertIn("last_tick_time", health)
        finally:
            temp.cleanup()

    def test_websocket_message_schema_and_replay_updates_forming_bar(self):
        temp, client = self.make_client()
        required = {
            "type", "symbol", "contract", "exchange_time", "received_time",
            "latency_ms", "time", "open", "high", "low", "close", "volume",
            "status", "connection_status",
        }
        try:
            with client:
                with client.websocket_connect("/ws/market/TMF") as socket:
                    forming_counts = {}
                    deadline = time.time() + 3
                    while time.time() < deadline and not any(
                        count >= 2 for count in forming_counts.values()
                    ):
                        message = socket.receive_json()
                        if message.get("type") == "kbar":
                            self.assertTrue(required.issubset(message))
                            if message["status"] == "forming":
                                bar_time = message["time"]
                                forming_counts[bar_time] = forming_counts.get(bar_time, 0) + 1
                    self.assertTrue(
                        any(count >= 2 for count in forming_counts.values()),
                        "expected repeated forming updates for the same minute",
                    )
        finally:
            temp.cleanup()

    def test_cloudflare_origin_auth_forwards_verified_identity(self):
        class FakeAccessValidator:
            def authenticate(self, token):
                if token != "signed-assertion":
                    raise AccessTokenError("invalid assertion")
                return AccessIdentity(subject="user-123", email="owner@example.com")

        temp = tempfile.TemporaryDirectory()
        repository = SQLiteBarRepository(Path(temp.name) / "auth.sqlite3")
        settings = LiveSettings(
            mode="mock", db_path=str(Path(temp.name) / "auth.sqlite3"),
            replay_csv=str(ROOT / "data/mock_tmf_ticks.csv"), replay_speed=1000,
            heartbeat_seconds=0.05,
        )
        feed = ReplayFeed(settings.replay_csv, speed=1000, loop=False)
        app = create_app(
            settings, feed=feed, repository=repository,
            access_validator=FakeAccessValidator(),
        )
        try:
            with TestClient(app) as client:
                denied = client.get("/internal/auth/cloudflare")
                self.assertEqual(denied.status_code, 401)
                accepted = client.get(
                    "/internal/auth/cloudflare",
                    headers={"Cf-Access-Jwt-Assertion": "signed-assertion"},
                )
                self.assertEqual(accepted.status_code, 204)
                self.assertEqual(
                    accepted.headers["x-authenticated-email"], "owner@example.com"
                )
                self.assertEqual(
                    accepted.headers["x-authenticated-subject"], "user-123"
                )
        finally:
            temp.cleanup()


class ReplayConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_heartbeat_and_stop(self):
        feed = ReplayFeed(ROOT / "data/mock_tmf_ticks.csv", speed=1000, loop=False)
        statuses = []
        ticks = []
        await feed.start(ticks.append, statuses.append)
        await asyncio.sleep(0.02)
        self.assertEqual(statuses[0], "connected")
        self.assertTrue(await feed.heartbeat() or len(ticks) == 12)
        await feed.stop()
        self.assertFalse(await feed.heartbeat())

    async def test_looping_replay_keeps_ticks_unique_and_time_monotonic(self):
        feed = ReplayFeed(ROOT / "data/mock_tmf_ticks.csv", speed=1000, loop=True)
        ticks = []
        try:
            await feed.start(ticks.append, lambda _status: None)
            deadline = time.time() + 2
            while len(ticks) < 24 and time.time() < deadline:
                await asyncio.sleep(0.02)
            self.assertGreaterEqual(len(ticks), 24)
            sample = ticks[:24]
            self.assertEqual(len({item.sequence for item in sample}), len(sample))
            self.assertEqual(
                [item.exchange_time for item in sample],
                sorted(item.exchange_time for item in sample),
            )
            self.assertLess(
                (sample[-1].exchange_time - sample[0].exchange_time).total_seconds(),
                3,
            )
        finally:
            await feed.stop()

    async def test_connection_events_recover_without_using_tick_timeout(self):
        class EventFeed:
            contract = "TMFI6"
            healthy = True

            async def start(self, _on_tick, on_status):
                self.on_status = on_status
                on_status("connected")

            async def stop(self):
                self.healthy = False

            async def heartbeat(self):
                return self.healthy

        temp = tempfile.TemporaryDirectory()
        repository = SQLiteBarRepository(Path(temp.name) / "events.sqlite3")
        feed = EventFeed()
        service = LiveMarketService(feed, repository, heartbeat_seconds=0.01)
        try:
            await service.start()
            feed.on_status("reconnecting")
            self.assertEqual(service.connection_status, "reconnecting")
            feed.on_status("connected")
            self.assertEqual(service.connection_status, "connected")
            # No tick was sent; connection remains healthy because heartbeat,
            # not tick recency, is authoritative.
            await asyncio.sleep(0.02)
            self.assertEqual(service.connection_status, "connected")
        finally:
            await service.stop()
            repository.close()
            temp.cleanup()

    async def test_restart_keeps_persistent_tick_deduplication(self):
        class ManualFeed:
            contract = "TMFI6"
            healthy = True
            async def start(self, on_tick, on_status):
                self.on_tick = on_tick
                on_status("connected")
            async def stop(self): self.healthy = False
            async def heartbeat(self): return self.healthy

        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "restart.sqlite3"
        tz = ZoneInfo("Asia/Taipei")
        when = datetime(2026, 8, 24, 15, 0, 1, tzinfo=tz)
        item = TickEvent(
            symbol="TMF", contract="TMFI6", exchange_time=when,
            received_time=when + timedelta(milliseconds=10), price=100,
            volume=2, sequence="persistent-1",
        )
        try:
            first_repo = SQLiteBarRepository(path)
            first_feed = ManualFeed()
            first = LiveMarketService(first_feed, first_repo, heartbeat_seconds=1)
            await first.start(); first_feed.on_tick(item); await first.queue.join(); await first.stop(); first_repo.close()

            second_repo = SQLiteBarRepository(path)
            second_feed = ManualFeed()
            second = LiveMarketService(second_feed, second_repo, heartbeat_seconds=1)
            await second.start(); second_feed.on_tick(item); await second.queue.join()
            bars = second_repo.latest("TMF", 10)
            self.assertEqual(bars[-1].volume, 2)
            self.assertEqual(second.aggregator.duplicate_ticks, 1)
            await second.stop(); second_repo.close()
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
