import unittest

import pandas as pd

from tw_quant.strategies import OpeningRangeBreakout, OpeningRangeBreakoutConfig


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


if __name__ == "__main__":
    unittest.main()

