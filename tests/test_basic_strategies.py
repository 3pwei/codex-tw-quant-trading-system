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


def bars(
    values: list[float],
    volumes: list[int] | None = None,
    start: datetime | None = None,
) -> list[KBar]:
    result = []
    for index, value in enumerate(values):
        timestamp = (start or datetime(2026, 9, 1, 15, 0, tzinfo=TAIPEI)) + timedelta(
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
                [100, 102, 106, 110, 108, 106, 105, 107, 111, 115, 113, 111, 110, 112, 116, 120, 125, 126],
                {},
                {"atr_period": 2, "pivot_reversal_atr": 0.1, "confirmation_bars": 1, "minimum_pivot_distance": 1, "minimum_channel_bars": 2},
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

    def test_dow_channel_requires_confirmed_market_structure(self):
        values = [100, 102, 106, 110, 108, 106, 105, 107, 111, 115, 113, 111, 110, 112, 116, 120, 125, 126]
        result = analyze_strategies(
            bars(values),
            ["linear_channel_breakout"],
            parameters={"linear_channel_breakout": {
                "atr_period": 2,
                "pivot_reversal_atr": 0.1,
                "confirmation_bars": 1,
                "minimum_pivot_distance": 1,
                "minimum_channel_bars": 2,
            }},
        )["strategies"][0]
        points = result["overlays"][0]["points"]
        self.assertTrue(points)
        self.assertEqual(result["overlays"][0]["model"], "dow_theory")
        self.assertEqual(points[0]["direction"], "up")
        self.assertGreater(points[0]["channel_start"], points[0]["anchor_2"])
        self.assertGreater(points[-1]["upper"], points[-1]["center"])
        self.assertGreater(points[-1]["center"], points[-1]["lower"])

    def test_dow_channel_overlay_combines_all_trading_sessions(self):
        values = [100, 102, 106, 110, 108, 106, 105, 107, 111, 115, 113, 111, 110, 112, 116, 120, 125, 126]
        result = analyze_strategies(
            bars(values) + bars(
                values,
                start=datetime(2026, 9, 2, 15, 0, tzinfo=TAIPEI),
            ),
            ["linear_channel_breakout"],
            parameters={"linear_channel_breakout": {
                "atr_period": 2,
                "pivot_reversal_atr": 0.1,
                "confirmation_bars": 1,
                "minimum_pivot_distance": 1,
                "minimum_channel_bars": 2,
            }},
        )["strategies"][0]
        self.assertEqual(len(result["overlays"]), 1)
        point_dates = {point["time"][:10] for point in result["overlays"][0]["points"]}
        self.assertEqual(point_dates, {"2026-09-01", "2026-09-02"})

    def test_legacy_regression_channel_parameters_migrate_to_dow_defaults(self):
        migrated = validate_strategy_parameters("linear_channel_breakout", {
            "minimum_lookback": 20,
            "maximum_lookback": 100,
            "lookback_step": 10,
            "boundary_quantile": 0.95,
            "minimum_r_squared": 0.55,
            "minimum_containment": 0.85,
            "stop_loss_pct": 0.01,
            "take_profit_pct": 0.02,
        })
        self.assertEqual(migrated["atr_period"], 14)
        self.assertEqual(migrated["stop_loss_pct"], 0.01)


if __name__ == "__main__":
    unittest.main()
