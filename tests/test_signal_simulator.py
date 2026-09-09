from datetime import date
import unittest

import pandas as pd

from tw_quant.execution import SignalSimulationPolicy, simulate_signals


def bars() -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-09-09 08:45",
        periods=5,
        freq="min",
        tz="Asia/Taipei",
    )
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": [100.0] * 5,
            "high": [100.1] * 5,
            "low": [99.9] * 5,
            "close": [100.0] * 5,
            "contract": ["TMFU6"] * 5,
            "session": ["day"] * 5,
            "trading_date": [date(2026, 9, 9).isoformat()] * 5,
        }
    )


class SignalSimulatorPolicyTests(unittest.TestCase):
    def test_default_policy_keeps_one_entry_per_group(self):
        frame = bars()
        entries = pd.Series([1, 0, 1, 0, 0], index=frame.index)
        exits = pd.DataFrame(
            {"long": [False, True, False, False, False],
             "short": [False] * 5},
            index=frame.index,
        )

        signals = simulate_signals(frame, "test", entries, exits)

        self.assertEqual(
            [signal["event"] for signal in signals],
            ["entry", "exit"],
        )

    def test_policy_can_allow_reentry_without_using_signal_history_as_state(self):
        frame = bars()
        entries = pd.Series([1, 0, 1, 0, 0], index=frame.index)
        exits = pd.DataFrame(
            {"long": [False, True, False, True, False],
             "short": [False] * 5},
            index=frame.index,
        )
        policy = SignalSimulationPolicy(
            max_entries_per_group=2,
            strategy_exit_reason="channel_invalidation",
        )

        signals = simulate_signals(
            frame,
            "test",
            entries,
            exits,
            policy=policy,
        )

        self.assertEqual(
            [signal["event"] for signal in signals],
            ["entry", "exit", "entry", "exit"],
        )
        self.assertEqual(signals[1]["reason"], "channel_invalidation")
        self.assertEqual(signals[3]["reason"], "channel_invalidation")

    def test_policy_rejects_invalid_configuration(self):
        with self.assertRaisesRegex(ValueError, "max_entries_per_group"):
            SignalSimulationPolicy(max_entries_per_group=0)
        with self.assertRaisesRegex(ValueError, "strategy_exit_reason"):
            SignalSimulationPolicy(strategy_exit_reason=" ")


if __name__ == "__main__":
    unittest.main()
