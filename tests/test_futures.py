import unittest

import pandas as pd

from tw_quant.futures import ticks_to_bars


class FuturesDataTests(unittest.TestCase):
    def test_ticks_are_aggregated_to_five_minute_bars(self):
        ticks = pd.DataFrame({
            "timestamp": pd.to_datetime([
                "2026-08-24 15:00:01+08:00",
                "2026-08-24 15:04:59+08:00",
                "2026-08-24 15:05:00+08:00",
            ]),
            "price": [100, 103, 102],
            "volume": [1, 2, 3],
        })
        bars = ticks_to_bars(
            ticks,
            interval="5min",
            session_start="2026-08-24 15:00",
        )
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars.iloc[0]["open"], 100)
        self.assertEqual(bars.iloc[0]["high"], 103)
        self.assertEqual(bars.iloc[0]["close"], 103)
        self.assertEqual(bars.iloc[0]["volume"], 3)


if __name__ == "__main__":
    unittest.main()
