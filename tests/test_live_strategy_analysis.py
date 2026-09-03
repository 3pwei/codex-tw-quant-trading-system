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
        self.assertEqual(signals[0]["price"], 103.0)
        self.assertEqual(signals[0]["stop_loss_price"], 102.382)
        self.assertEqual(signals[0]["take_profit_price"], 104.236)

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
        self.assertEqual(signals[0]["stop_loss_price"], 89.46)
        self.assertEqual(signals[0]["take_profit_price"], 91.08)

    def test_short_entry_has_inverted_stop_and_take_profit_prices(self):
        bars = [bar(index, 100.0) for index in range(15)]
        bars.append(bar(15, 98.0, volume=500))
        bars.append(bar(16, 97.0))
        signals = analyze_live_strategies(bars, ["orb"])["strategies"][0]["signals"]
        self.assertEqual(signals[0]["direction"], "short")
        self.assertEqual(signals[0]["price"], 97.0)
        self.assertEqual(signals[0]["stop_loss_price"], 97.582)
        self.assertEqual(signals[0]["take_profit_price"], 95.836)

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
