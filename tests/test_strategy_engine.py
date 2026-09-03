from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from tw_quant.market import KBar
from tw_quant.strategy import analyze_strategies
from tw_quant.strategy import validate_strategy_parameters


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


class StrategyAnalysisTests(unittest.TestCase):
    def test_custom_orb_and_risk_parameters_change_shared_signals(self):
        bars = [bar(0, 100.0), bar(1, 100.0)]
        bars.append(bar(2, 102.0, volume=500))
        bars.append(bar(3, 103.0))
        result = analyze_strategies(
            bars,
            ["orb"],
            parameters={
                "orb": {
                    "opening_range_minutes": 2,
                    "volume_window": 2,
                    "volume_multiplier": 1.0,
                    "stop_loss_pct": 0.01,
                    "take_profit_pct": 0.03,
                }
            },
        )
        entry = result["strategies"][0]["signals"][0]
        self.assertEqual(entry["price"], 103.0)
        self.assertEqual(entry["stop_loss_price"], 101.97)
        self.assertEqual(entry["take_profit_price"], 106.09)

    def test_invalid_cross_parameter_rules_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "exit_z_score"):
            validate_strategy_parameters(
                "bnf", {"entry_z_score": 1.0, "exit_z_score": 1.0}
            )

    def test_orb_breakout_is_filled_on_next_bar_open(self):
        bars = [bar(index, 100.0) for index in range(15)]
        bars.append(bar(15, 102.0, volume=500))
        bars.append(bar(16, 103.0))
        result = analyze_strategies(bars, ["orb"])
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
        result = analyze_strategies(bars, ["bnf"])
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
        signals = analyze_strategies(bars, ["orb"])["strategies"][0]["signals"]
        self.assertEqual(signals[0]["direction"], "short")
        self.assertEqual(signals[0]["price"], 97.0)
        self.assertEqual(signals[0]["stop_loss_price"], 97.582)
        self.assertEqual(signals[0]["take_profit_price"], 95.836)

    def test_forming_bar_does_not_confirm_bnf_signal(self):
        bars = [bar(index, 100.0) for index in range(20)]
        bars.append(bar(20, 90.0, volume=500, status="forming"))
        result = analyze_strategies(bars, ["bnf"])
        self.assertEqual(result["strategies"][0]["signals"], [])

    def test_strategy_filter_and_unknown_strategy(self):
        result = analyze_strategies([], ["bnf"])
        self.assertEqual(
            [item["key"] for item in result["strategies"]], ["bnf"]
        )
        with self.assertRaisesRegex(ValueError, "unsupported strategies"):
            analyze_strategies([], ["unknown"])


if __name__ == "__main__":
    unittest.main()
