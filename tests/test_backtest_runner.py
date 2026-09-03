from datetime import date, datetime, timedelta
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from tw_quant.backtest import run_strategy_backtest, validate_date_range
from tw_quant.market import KBar
from tw_quant.live.storage import SQLiteBarRepository


TAIPEI = ZoneInfo("Asia/Taipei")


def make_bar(minute: int, close: float, volume: int = 100) -> KBar:
    timestamp = datetime(2026, 8, 24, 15, 0, tzinfo=TAIPEI) + timedelta(minutes=minute)
    return KBar(
        symbol="TMF", contract="TMFU6", time=timestamp, open=close,
        high=close + 0.2, low=close - 0.2, close=close, volume=volume,
        status="closed", session="night", trading_date=date(2026, 8, 25),
        first_tick_time=timestamp, last_tick_time=timestamp + timedelta(seconds=50),
        exchange_time=timestamp + timedelta(seconds=50),
        received_time=timestamp + timedelta(seconds=50, milliseconds=10),
        latency_ms=10,
    )


class StrategyBacktestTests(unittest.TestCase):
    def test_range_is_inclusive_and_limited_to_31_days(self):
        validate_date_range(date(2026, 8, 1), date(2026, 8, 31))
        with self.assertRaisesRegex(ValueError, "31 天"):
            validate_date_range(date(2026, 8, 1), date(2026, 9, 1))
        with self.assertRaisesRegex(ValueError, "開始日期"):
            validate_date_range(date(2026, 8, 2), date(2026, 8, 1))

    def test_orb_backtest_uses_shared_signals_and_forces_last_session_exit(self):
        bars = [make_bar(index, 100.0) for index in range(15)]
        bars += [make_bar(15, 102.0, 500), make_bar(16, 103.0)]
        result = run_strategy_backtest(
            bars, "orb", date(2026, 8, 25), date(2026, 8, 25)
        )
        self.assertEqual(result["metadata"]["strategy_key"], "orb")
        self.assertEqual(result["metadata"]["interval"], "1 分鐘")
        self.assertEqual(len(result["trades"]), 1)
        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "session_end")
        self.assertEqual(trade["stop_loss_price"], 102.382)
        self.assertEqual(trade["take_profit_price"], 104.236)

    def test_bnf_backtest_uses_the_same_mean_reversion_signal_core(self):
        bars = [make_bar(index, 100.0) for index in range(20)]
        bars += [make_bar(20, 90.0, 500), make_bar(21, 90.0)]
        result = run_strategy_backtest(
            bars, "bnf", date(2026, 8, 25), date(2026, 8, 25)
        )
        self.assertEqual(result["metadata"]["strategy_key"], "bnf")
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(result["trades"][0]["direction"], "long")
        self.assertEqual(result["trades"][0]["stop_loss_price"], 89.46)
        self.assertEqual(result["trades"][0]["take_profit_price"], 91.08)

    def test_backtest_accepts_the_same_custom_strategy_parameters(self):
        bars = [make_bar(index, 100.0) for index in range(2)]
        bars += [make_bar(2, 102.0, 500), make_bar(3, 103.0)]
        result = run_strategy_backtest(
            bars,
            "orb",
            date(2026, 8, 25),
            date(2026, 8, 25),
            parameters={
                "opening_range_minutes": 2,
                "volume_window": 2,
                "volume_multiplier": 1,
                "stop_loss_pct": 0.01,
                "take_profit_pct": 0.03,
            },
        )
        self.assertEqual(result["config"]["opening_range_minutes"], 2)
        self.assertEqual(result["config"]["stop_loss_pct"], 0.01)
        self.assertEqual(result["trades"][0]["stop_loss_price"], 101.97)
        self.assertEqual(result["trades"][0]["take_profit_price"], 106.09)

    def test_backtest_reports_selected_timeframe(self):
        bars = [make_bar(index, 100.0) for index in range(20)]
        result = run_strategy_backtest(
            bars, "orb", date(2026, 8, 25), date(2026, 8, 25), interval="5m"
        )
        self.assertEqual(result["metadata"]["interval"], "5 分鐘")
        self.assertEqual(result["metadata"]["interval_key"], "5m")
        self.assertEqual(result["config"]["bar_minutes"], 5)

    def test_repository_filters_closed_bars_by_trading_date(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = SQLiteBarRepository(Path(directory) / "bars.sqlite3")
            try:
                first = make_bar(0, 100)
                forming = make_bar(1, 101)
                forming.status = "forming"
                repo.save(first)
                repo.save(forming)
                self.assertEqual(
                    repo.date_bounds("TMF"),
                    (date(2026, 8, 25), date(2026, 8, 25)),
                )
                selected = repo.between_trading_dates(
                    "TMF", date(2026, 8, 25), date(2026, 8, 25)
                )
                self.assertEqual([bar.time for bar in selected], [first.time])
            finally:
                repo.close()

    def test_strategy_parameters_persist_across_repository_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bars.sqlite3"
            first = SQLiteBarRepository(path)
            first.save_strategy_parameters(
                "orb",
                {
                    "opening_range_minutes": 10,
                    "volume_window": 8,
                    "volume_multiplier": 1.5,
                    "stop_loss_pct": 0.01,
                    "take_profit_pct": 0.025,
                },
            )
            first.close()
            reopened = SQLiteBarRepository(path)
            try:
                self.assertEqual(
                    reopened.strategy_parameters()["orb"]["opening_range_minutes"],
                    10,
                )
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
