from datetime import time
import unittest

import pandas as pd

from tw_quant.config import BacktestConfig, CostConfig
from tw_quant.engine import BacktestEngine


class FixedSignalStrategy:
    def __init__(self, signal_index: int, signal: int = 1):
        self.signal_index = signal_index
        self.signal = signal

    def generate_entries(self, day_bars):
        result = pd.Series(0, index=day_bars.index, dtype="int8")
        result.iloc[self.signal_index] = self.signal
        return result


def make_bars(count=10):
    timestamps = pd.date_range(
        "2026-01-02 09:00",
        periods=count,
        freq="1min",
        tz="Asia/Taipei",
    )
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "symbol": ["2330"] * count,
            "open": [100.0 + i for i in range(count)],
            "high": [100.2 + i for i in range(count)],
            "low": [99.8 + i for i in range(count)],
            "close": [100.1 + i for i in range(count)],
            "volume": [1000] * count,
        }
    )


def zero_costs():
    return CostConfig(
        commission_rate=0,
        min_commission=0,
        sell_tax_rate=0,
        slippage_bps=0,
    )


class EngineTests(unittest.TestCase):
    def test_signal_executes_at_next_bar_open(self):
        bars = make_bars()
        engine = BacktestEngine(
            FixedSignalStrategy(signal_index=2),
            BacktestConfig(
                stop_loss_pct=0.5,
                take_profit_pct=0.5,
                force_exit_time=time(9, 8),
            ),
            zero_costs(),
        )
        result = engine.run(bars)
        trade = result.trades.iloc[0]
        self.assertEqual(trade["entry_time"], bars.iloc[3]["timestamp"])
        self.assertEqual(trade["entry_price"], bars.iloc[3]["open"])
        self.assertEqual(trade["exit_time"], bars.iloc[8]["timestamp"])
        self.assertEqual(trade["exit_price"], bars.iloc[8]["open"])
        self.assertEqual(trade["exit_reason"], "force_exit")

    def test_stop_has_priority_if_stop_and_target_touch_same_bar(self):
        bars = make_bars(4)
        bars.loc[1, ["open", "high", "low", "close"]] = [100, 102, 98, 100]
        engine = BacktestEngine(
            FixedSignalStrategy(signal_index=0),
            BacktestConfig(stop_loss_pct=0.01, take_profit_pct=0.01),
            zero_costs(),
        )
        trade = engine.run(bars).trades.iloc[0]
        self.assertEqual(trade["exit_reason"], "stop_loss")
        self.assertEqual(trade["exit_price"], 99.0)


if __name__ == "__main__":
    unittest.main()

