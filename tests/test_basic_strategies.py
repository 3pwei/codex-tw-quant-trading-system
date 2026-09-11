from datetime import datetime, timedelta
import unittest
from zoneinfo import ZoneInfo

from tw_quant.market import KBar
from tw_quant.strategy import (
    SUPPORTED_STRATEGIES,
    analyze_strategies,
    default_composite_definition,
    strategy_catalog,
    validate_composite_definition,
    validate_strategy_parameters,
)


TAIPEI = ZoneInfo("Asia/Taipei")
DOW_PARAMETERS = {
    "atr_period": 2,
    "pivot_reversal_atr": 0.1,
    "confirmation_bars": 1,
    "minimum_pivot_distance": 1,
    "minimum_channel_bars": 2,
    "invalidation_bars": 2,
    "stop_loss_pct": 0.2,
    "take_profit_pct": 0.5,
}
UP_CHANNEL_VALUES = [
    100, 102, 106, 110, 108, 106, 105, 107, 111,
    115, 113, 111, 110, 112, 116, 120, 125, 126,
]
DOWN_CHANNEL_VALUES = [200 - (value - 100) for value in UP_CHANNEL_VALUES]


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
            "dow_channel_pullback": "Trend",
            "dow_channel_reversal": "Reversal",
            "linear_channel_breakout": "Momentum",
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
        result = analyze_strategies(
            bars(UP_CHANNEL_VALUES),
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
        result = analyze_strategies(
            bars(UP_CHANNEL_VALUES) + bars(
                UP_CHANNEL_VALUES,
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

    def test_dow_channel_exit_has_domain_specific_reason(self):
        values = [
            100, 102, 106, 110, 108, 106, 105, 107, 111,
            115, 113, 111, 110, 112, 116, 120, 125, 126,
            127, 126, 115, 114, 113,
        ]
        result = analyze_strategies(
            bars(values),
            ["linear_channel_breakout"],
            parameters={"linear_channel_breakout": {
                "atr_period": 2,
                "pivot_reversal_atr": 0.1,
                "confirmation_bars": 1,
                "minimum_pivot_distance": 1,
                "minimum_channel_bars": 2,
                "invalidation_bars": 2,
                "stop_loss_pct": 0.2,
                "take_profit_pct": 0.5,
            }},
        )["strategies"][0]

        exits = [
            signal for signal in result["signals"]
            if signal["event"] == "exit"
        ]
        self.assertEqual(exits[0]["reason"], "channel_invalidation")
        self.assertEqual(
            exits[0]["time"],
            bars(values)[22].time.isoformat(timespec="milliseconds"),
        )

    def test_pullback_enters_long_on_next_open_after_lower_boundary_reclaim(self):
        source = bars([*UP_CHANNEL_VALUES, 115, 116])
        source[18].low = 114.9
        result = analyze_strategies(
            source,
            ["dow_channel_pullback"],
            parameters={"dow_channel_pullback": {
                **DOW_PARAMETERS, "boundary_tolerance_atr": 0.2,
            }},
        )["strategies"][0]
        entries = [item for item in result["signals"] if item["event"] == "entry"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["direction"], "long")
        self.assertEqual(
            entries[0]["time"],
            source[19].time.isoformat(timespec="milliseconds"),
        )
        self.assertEqual(entries[0]["price"], source[19].open)

    def test_pullback_enters_short_on_next_open_after_upper_boundary_rejection(self):
        source = bars([*DOWN_CHANNEL_VALUES, 185, 184])
        source[18].high = 185.1
        result = analyze_strategies(
            source,
            ["dow_channel_pullback"],
            parameters={"dow_channel_pullback": {
                **DOW_PARAMETERS, "boundary_tolerance_atr": 0.2,
            }},
        )["strategies"][0]
        entries = [item for item in result["signals"] if item["event"] == "entry"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["direction"], "short")
        self.assertEqual(
            entries[0]["time"],
            source[19].time.isoformat(timespec="milliseconds"),
        )

    def test_pullback_exits_only_after_formal_channel_invalidation(self):
        source = bars([*UP_CHANNEL_VALUES, 115, 116, 110, 109, 108])
        signals = analyze_strategies(
            source,
            ["dow_channel_pullback"],
            parameters={"dow_channel_pullback": {
                **DOW_PARAMETERS, "boundary_tolerance_atr": 0.2,
            }},
        )["strategies"][0]["signals"]
        self.assertEqual(
            [(item["event"], item["time"], item["reason"]) for item in signals],
            [
                (
                    "entry", source[19].time.isoformat(timespec="milliseconds"),
                    "signal_confirmed",
                ),
                (
                    "exit", source[22].time.isoformat(timespec="milliseconds"),
                    "channel_invalidation",
                ),
            ],
        )

    def test_reversal_breaks_up_channel_short_without_immediate_strategy_exit(self):
        source = bars([*UP_CHANNEL_VALUES, 110, 109])
        signals = analyze_strategies(
            source,
            ["dow_channel_reversal"],
            parameters={"dow_channel_reversal": DOW_PARAMETERS},
        )["strategies"][0]["signals"]
        self.assertEqual(
            [(item["event"], item["direction"]) for item in signals],
            [("entry", "short")],
        )
        self.assertEqual(
            signals[0]["time"],
            source[19].time.isoformat(timespec="milliseconds"),
        )

    def test_reversal_breaks_down_channel_long(self):
        source = bars([*DOWN_CHANNEL_VALUES, 190, 191])
        signals = analyze_strategies(
            source,
            ["dow_channel_reversal"],
            parameters={"dow_channel_reversal": DOW_PARAMETERS},
        )["strategies"][0]["signals"]
        self.assertEqual(
            [(item["event"], item["direction"]) for item in signals],
            [("entry", "long")],
        )

    def test_momentum_preserves_up_and_down_channel_breakouts(self):
        for values, direction in (
            (UP_CHANNEL_VALUES, "long"),
            (DOWN_CHANNEL_VALUES, "short"),
        ):
            with self.subTest(direction=direction):
                entries = [
                    item for item in analyze_strategies(
                        bars(values),
                        ["linear_channel_breakout"],
                        parameters={"linear_channel_breakout": DOW_PARAMETERS},
                    )["strategies"][0]["signals"]
                    if item["event"] == "entry"
                ]
                self.assertEqual(entries[0]["direction"], direction)

    def test_unconfirmed_pivot_and_forming_boundary_cross_emit_no_signal(self):
        unconfirmed = bars(UP_CHANNEL_VALUES[:14])
        unconfirmed[-1].status = "forming"
        result = analyze_strategies(
            unconfirmed,
            ["linear_channel_breakout"],
            parameters={"linear_channel_breakout": DOW_PARAMETERS},
        )["strategies"][0]
        self.assertEqual(result["overlays"][0]["points"], [])
        self.assertEqual(result["signals"], [])

        forming_cross = bars(UP_CHANNEL_VALUES)
        forming_cross[16].status = "forming"
        signals = analyze_strategies(
            forming_cross,
            ["linear_channel_breakout"],
            parameters={"linear_channel_breakout": DOW_PARAMETERS},
        )["strategies"][0]["signals"]
        self.assertFalse(any(item["event"] == "entry" for item in signals))

    def test_pullback_tolerance_and_legacy_composite_reference_are_valid(self):
        parameters = validate_strategy_parameters("dow_channel_pullback", {})
        self.assertEqual(parameters["boundary_tolerance_atr"], 0.2)
        with self.assertRaises(ValueError):
            validate_strategy_parameters(
                "dow_channel_pullback", {"boundary_tolerance_atr": 3.1}
            )

        definition = default_composite_definition()
        definition["entry"]["rules"] = [{
            "strategy": "linear_channel_breakout", "interval": "1m",
        }]
        validated = validate_composite_definition(definition)
        self.assertEqual(
            validated["entry"]["rules"][0]["strategy"],
            "linear_channel_breakout",
        )

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
