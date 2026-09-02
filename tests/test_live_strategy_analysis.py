from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from tw_quant.live.models import KBar
from tw_quant.live.strategy_analysis import analyze_live_strategies


TAIPEI = ZoneInfo("Asia/Taipei")


def bar(minute: int, close: float, volume: int = 100, status: str = "closed") -> KBar:
    timestamp = datetime(2026, 8, 24, 15, 0, tzinfo=TAIPEI) + timedelta(minutes=minute)
    return KBar(
        symbol="TMF",
        contract="TMFU6",
        time=timestamp,
        open=close,
        high=close + 0.2,
        low=close - 0.2,
        close=close,
        volume=volume,
        status=status,
        session="night",
        trading_date=(timestamp + timedelta(days=1)).date(),
        first_tick_time=timestamp,
        last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=20),
        latency_ms=20,
    )


class LiveStrategyAnalysisTests(unittest.TestCase):
    def test_orb_breakout_is_filled_on_next_bar_open(self):
        bars = [bar(index, 100.0) for index in range(15)]
        bars.append(bar(15, 102.0, volume=500))
        bars.append(bar(16, 103.0))
        result = analyze_live_strategies(bars, ["orb"])
        signals = result["strategies"][0]["signals"]
        self.assertEqual(signals[0]["event"], "entry")
        self.assertEqual(signals[0]["direction"], "long")
        self.assertEqual(
            signals[0]["time"],
            bars[16].time.isoformat(timespec="milliseconds"),
        )

    def test_bnf_downside_stretch_emits_long_entry(self):
        bars = [bar(index, 100.0) for index in range(20)]
        bars.append(bar(20, 90.0, volume=500))
        bars.append(bar(21, 90.0))
        result = analyze_live_strategies(bars, ["bnf"])
        signals = result["strategies"][0]["signals"]
        self.assertEqual(signals[0]["strategy"], "bnf")
        self.assertEqual(signals[0]["event"], "entry")
        self.assertEqual(signals[0]["direction"], "long")
        self.assertEqual(
            signals[0]["time"],
            bars[21].time.isoformat(timespec="milliseconds"),
        )

    def test_forming_bar_does_not_confirm_bnf_signal(self):
        bars = [bar(index, 100.0) for index in range(20)]
        bars.append(bar(20, 90.0, volume=500, status="forming"))
        result = analyze_live_strategies(bars, ["bnf"])
        self.assertEqual(result["strategies"][0]["signals"], [])

    def test_strategy_filter_and_unknown_strategy(self):
        result = analyze_live_strategies([], ["bnf"])
        self.assertEqual(
            [item["key"] for item in result["strategies"]], ["bnf"]
        )
        with self.assertRaisesRegex(ValueError, "unsupported strategies"):
            analyze_live_strategies([], ["unknown"])


if __name__ == "__main__":
    unittest.main()
