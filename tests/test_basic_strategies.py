from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from tw_quant.market import KBar
from tw_quant.strategy import (
    SUPPORTED_STRATEGIES,
    analyze_strategies,
    strategy_catalog,
    validate_strategy_parameters,
)


TAIPEI = ZoneInfo("Asia/Taipei")


def bars(values: list[float], volumes: list[int] | None = None) -> list[KBar]:
    result = []
    for index, value in enumerate(values):
        timestamp = datetime(2026, 9, 1, 15, 0, tzinfo=TAIPEI) + timedelta(
            minutes=index
        )
        result.append(KBar(
            symbol="TMF",
            contract="TMFU6",
            time=timestamp,
            open=value,
            high=value + 0.2,
            low=value - 0.2,
            close=value,
            volume=(volumes or [100] * len(values))[index],
            status="closed",
            session="night",
            trading_date=(timestamp + timedelta(days=1)).date(),
            first_tick_time=timestamp,
            last_tick_time=timestamp + timedelta(seconds=50),
            exchange_time=timestamp + timedelta(seconds=50),
            received_time=timestamp + timedelta(seconds=50, milliseconds=20),
            latency_ms=20,
        ))
    return result


class BasicStrategyTests(unittest.TestCase):
    def test_catalog_contains_requested_strategy_families(self):
        catalog = {item["key"]: item for item in strategy_catalog()}
        expected = {
            "ma_crossover": "Trend",
            "ema_trend": "Trend",
            "donchian_breakout": "Breakout",
            "linear_channel_breakout": "Breakout",
            "orb": "Breakout",
            "rsi_mean_reversion": "Mean Reversion",
            "bollinger_mean_reversion": "Mean Reversion",
            "macd_momentum": "Momentum",
            "vwap_reversion": "Intraday",
            "atr_breakout": "Volatility",
            "volume_breakout": "Momentum",
        }
        self.assertTrue(expected.keys() <= catalog.keys())
        self.assertEqual(
            {key: catalog[key]["category"] for key in expected}, expected
        )
        self.assertEqual(set(catalog), set(SUPPORTED_STRATEGIES))

    def test_each_new_strategy_emits_a_confirmed_entry(self):
        cases = {
            "ma_crossover": (
                [100, 99, 98, 97, 96, 95, 96, 97, 98],
                {},
                {"short_window": 2, "long_window": 4},
            ),
            "ema_trend": (
                [100, 99, 98, 97, 96, 95, 96, 97, 98, 99],
                {},
                {"fast_period": 2, "slow_period": 4},
            ),
            "donchian_breakout": (
                [100, 100, 100, 102, 103],
                {},
                {"lookback_period": 3},
            ),
            "linear_channel_breakout": (
                [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 125, 126],
                {},
                {"minimum_lookback": 10, "maximum_lookback": 20, "lookback_step": 5, "minimum_r_squared": 0.5},
            ),
            "rsi_mean_reversion": (
                [100, 101, 102, 90, 91],
                {},
                {"rsi_period": 2},
            ),
            "bollinger_mean_reversion": (
                [100, 100, 100, 90, 91],
                {},
                {"window": 3, "std_multiplier": 1},
            ),
            "macd_momentum": (
                [100, 99, 98, 97, 96, 95, 96, 97, 98, 99, 100],
                {},
                {"fast_period": 2, "slow_period": 4, "signal_period": 2},
            ),
            "vwap_reversion": (
                [100, 100, 95, 96],
                {},
                {"entry_deviation_pct": 0.01, "exit_deviation_pct": 0.001},
            ),
            "atr_breakout": (
                [100, 100, 104, 105],
                {},
                {"atr_period": 2, "atr_multiplier": 1},
            ),
            "volume_breakout": (
                [100, 100, 103, 104],
                {"volumes": [100, 100, 500, 100]},
                {
                    "price_lookback": 2,
                    "volume_window": 2,
                    "volume_multiplier": 2,
                },
            ),
        }
        for key, (values, bar_options, parameters) in cases.items():
            with self.subTest(strategy=key):
                result = analyze_strategies(
                    bars(values, **bar_options),
                    [key],
                    parameters={key: parameters},
                )["strategies"][0]
                entries = [
                    signal for signal in result["signals"]
                    if signal["event"] == "entry"
                ]
                self.assertTrue(entries)
                self.assertEqual(entries[0]["strategy"], key)
                self.assertIn(entries[0]["direction"], {"long", "short"})

    def test_cross_parameter_rules_are_rejected(self):
        invalid = {
            "ma_crossover": {"short_window": 20, "long_window": 10},
            "ema_trend": {"fast_period": 26, "slow_period": 12},
            "macd_momentum": {"fast_period": 26, "slow_period": 12},
            "rsi_mean_reversion": {
                "oversold_rsi": 30,
                "exit_rsi": 75,
                "overbought_rsi": 70,
            },
            "vwap_reversion": {
                "entry_deviation_pct": 0.005,
                "exit_deviation_pct": 0.006,
            },
        }
        for key, parameters in invalid.items():
            with self.subTest(strategy=key), self.assertRaises(ValueError):
                validate_strategy_parameters(key, parameters)

    def test_linear_channel_overlay_is_as_of_and_matches_bars(self):
        result = analyze_strategies(
            bars([100 + index for index in range(21)]),
            ["linear_channel_breakout"],
            parameters={"linear_channel_breakout": {
                "minimum_lookback": 10,
                "maximum_lookback": 20,
                "lookback_step": 5,
                "minimum_r_squared": 0.5,
            }},
        )["strategies"][0]
        points = result["overlays"][0]["points"]
        self.assertTrue(points)
        self.assertEqual(points[0]["time"], bars([0] * 11)[-1].time.isoformat(timespec="milliseconds"))
        self.assertGreater(points[-1]["upper"], points[-1]["center"])
        self.assertGreater(points[-1]["center"], points[-1]["lower"])


if __name__ == "__main__":
    unittest.main()
