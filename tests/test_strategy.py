import unittest

import pandas as pd

from tw_quant.strategy import (
    BNFMeanReversion,
    BNFMeanReversionConfig,
    OpeningRangeBreakout,
    OpeningRangeBreakoutConfig,
)


class StrategyTests(unittest.TestCase):
    def test_orb_emits_first_breakout_only(self):
        timestamps = pd.date_range(
            "2026-01-02 09:00",
            periods=10,
            freq="1min",
            tz="Asia/Taipei",
        )
        bars = pd.DataFrame(
            {
                "timestamp": timestamps,
                "symbol": ["2330"] * 10,
                "open": [100] * 10,
                "high": [100.5] * 5 + [101.2] * 5,
                "low": [99.5] * 10,
                "close": [100] * 5 + [101, 101.1, 101, 101, 101],
                "volume": [100] * 5 + [300] * 5,
            }
        )
        strategy = OpeningRangeBreakout(
            OpeningRangeBreakoutConfig(
                opening_range_minutes=5,
                volume_window=3,
                volume_multiplier=1.2,
            )
        )
        signals = strategy.generate_entries(bars)
        self.assertEqual(int((signals != 0).sum()), 1)
        self.assertEqual(int(signals.iloc[5]), 1)

    def test_bnf_emits_long_entry_after_downside_z_score_cross(self):
        close = [100.0] * 20 + [90.0]
        bars = pd.DataFrame({"close": close})
        strategy = BNFMeanReversion(
            BNFMeanReversionConfig(
                mean_window=5,
                std_window=5,
                entry_z_score=1.5,
                exit_z_score=0.5,
                rsi_period=3,
            )
        )
        signals = strategy.generate_entries(bars)
        self.assertEqual(int(signals.iloc[-1]), 1)

    def test_bnf_exits_long_after_price_returns_to_mean(self):
        bars = pd.DataFrame({"close": [100.0] * 5 + [90.0, 98.0, 100.0]})
        strategy = BNFMeanReversion(
            BNFMeanReversionConfig(
                mean_window=5,
                std_window=5,
                entry_z_score=1.5,
                exit_z_score=0.5,
                rsi_period=3,
            )
        )
        exits = strategy.generate_exits(bars)
        self.assertTrue(bool(exits.iloc[-1]["long"]))


if __name__ == "__main__":
    unittest.main()
