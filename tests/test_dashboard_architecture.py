import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "dashboard" / "app"


class DashboardArchitectureTests(unittest.TestCase):
    def source(self, relative_path: str) -> str:
        return (DASHBOARD / relative_path).read_text(encoding="utf-8")

    def test_live_page_delegates_chart_and_socket_lifecycles(self):
        source = self.source("live/live-dashboard.tsx")
        self.assertIn("useMarketSocket", source)
        self.assertIn("useTradingChart", source)
        self.assertNotIn("new WebSocket", source)
        self.assertNotIn("createChart", source)

    def test_paper_page_delegates_account_polling(self):
        source = self.source("paper/paper-trading-dashboard.tsx")
        self.assertIn("usePaperAccount", source)
        self.assertNotIn("setInterval", source)
        self.assertNotIn("NEXT_PUBLIC_MARKET_API_URL", source)

    def test_replay_page_delegates_chart_and_uses_shared_client(self):
        source = self.source("replay/replay-dashboard.tsx")
        self.assertIn("useReplayChart", source)
        self.assertIn("../lib/api-client", source)
        self.assertNotIn("createChart", source)
        self.assertNotIn("NEXT_PUBLIC_MARKET_API_URL", source)


if __name__ == "__main__":
    unittest.main()
