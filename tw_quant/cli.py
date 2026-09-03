from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

import pandas as pd

from .backtest import run_strategy_backtest
from .config import BacktestConfig, CostConfig
from .data import load_bars
from .demo_data import save_demo_csv
from .engine import BacktestEngine
from .futures import (
    FuturesCostConfig,
    load_taifex_ticks,
    taifex_bars_to_kbars,
    ticks_to_bars,
)
from .report import save_report
from .strategy import (
    BNFMeanReversion,
    BNFMeanReversionConfig,
    OpeningRangeBreakout,
    OpeningRangeBreakoutConfig,
)


def parse_time(value: str):
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("時間格式必須是 HH:MM") from exc


def add_backtest_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output", default="output/backtest", help="輸出目錄")
    parser.add_argument("--initial-capital", type=float, default=1_000_000)
    parser.add_argument("--quantity", type=int, default=1_000, help="每筆股數；整股通常為 1000 股")
    parser.add_argument("--stop-loss", type=float, default=0.006, help="停損比例，例如 0.006")
    parser.add_argument("--take-profit", type=float, default=0.012, help="停利比例，例如 0.012")
    parser.add_argument("--force-exit", type=parse_time, default=parse_time("13:20"))
    parser.add_argument("--direction", choices=("long", "short", "both"), default="long")
    parser.add_argument("--strategy", choices=("orb", "bnf"), default="orb")
    parser.add_argument("--opening-minutes", type=int, default=15)
    parser.add_argument("--volume-window", type=int, default=5)
    parser.add_argument("--volume-multiplier", type=float, default=1.2)
    parser.add_argument("--bnf-window", type=int, default=20)
    parser.add_argument("--bnf-entry-z", type=float, default=2.0)
    parser.add_argument("--bnf-exit-z", type=float, default=0.5)
    parser.add_argument("--bnf-rsi-period", type=int, default=14)
    parser.add_argument("--last-entry", type=parse_time, default=parse_time("12:45"))
    parser.add_argument("--commission-rate", type=float, default=0.001425)
    parser.add_argument("--commission-discount", type=float, default=1.0)
    parser.add_argument("--min-commission", type=float, default=20.0)
    parser.add_argument("--sell-tax-rate", type=float, default=0.0015)
    parser.add_argument("--slippage-bps", type=float, default=2.0)


def make_engine(args: argparse.Namespace) -> BacktestEngine:
    if args.strategy == "bnf":
        strategy = BNFMeanReversion(
            BNFMeanReversionConfig(
                mean_window=args.bnf_window,
                std_window=args.bnf_window,
                entry_z_score=args.bnf_entry_z,
                exit_z_score=args.bnf_exit_z,
                rsi_period=args.bnf_rsi_period,
                direction=args.direction,
            )
        )
    else:
        strategy = OpeningRangeBreakout(
            OpeningRangeBreakoutConfig(
                opening_range_minutes=args.opening_minutes,
                volume_window=args.volume_window,
                volume_multiplier=args.volume_multiplier,
                last_entry_time=args.last_entry,
                direction=args.direction,
            )
        )
    backtest = BacktestConfig(
        initial_capital=args.initial_capital,
        quantity=args.quantity,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        force_exit_time=args.force_exit,
        allow_short=args.direction in ("short", "both"),
    )
    costs = CostConfig(
        commission_rate=args.commission_rate,
        commission_discount=args.commission_discount,
        min_commission=args.min_commission,
        sell_tax_rate=args.sell_tax_rate,
        slippage_bps=args.slippage_bps,
    )
    return BacktestEngine(strategy, backtest, costs)


def format_value(value) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def print_summary(summary: dict) -> None:
    labels = {
        "trades": "交易次數",
        "win_rate_pct": "勝率 (%)",
        "net_profit": "淨利 (NTD)",
        "return_pct": "報酬率 (%)",
        "profit_factor": "Profit Factor",
        "max_drawdown": "最大回撤 (NTD)",
        "max_drawdown_pct": "最大回撤 (%)",
        "total_cost": "總交易成本 (NTD)",
        "daily_sharpe": "日頻 Sharpe",
    }
    print("\n回測摘要")
    for key, label in labels.items():
        print(f"  {label:<20} {format_value(summary.get(key))}")


def run_backtest(args: argparse.Namespace) -> None:
    bars = load_bars(args.csv, getattr(args, "symbol", None))
    result = make_engine(args).run(bars)
    files = save_report(result, args.output)
    print_summary(result.summary)
    print(f"\n報告位置：{Path(args.output).resolve()}")
    for name, path in files.items():
        print(f"  {name:<8} {path.name}")


def run_demo(args: argparse.Namespace) -> None:
    output = Path(args.output)
    demo_csv = output / "demo_2330_1m.csv"
    save_demo_csv(demo_csv)
    args.csv = str(demo_csv)
    args.symbol = "2330"
    print("已產生合成示範資料（非真實行情）。")
    run_backtest(args)


def run_futures_night(args: argparse.Namespace) -> None:
    ticks = load_taifex_ticks(
        args.csv,
        product=args.product,
        contract_month=args.contract_month,
        session_start=args.session_start,
        session_end=args.session_end,
    )
    bars = ticks_to_bars(
        ticks,
        interval="1min",
        session_start=args.session_start,
        symbol=args.product,
    )
    canonical_bars = taifex_bars_to_kbars(
        bars,
        symbol=args.product,
        contract=f"{args.product}{args.contract_month}",
        interval="1min",
    )
    if not canonical_bars:
        raise ValueError("指定條件沒有可回測的期交所成交資料")
    start = min(bar.trading_date for bar in canonical_bars)
    end = max(bar.trading_date for bar in canonical_bars)
    result = run_strategy_backtest(
        canonical_bars,
        args.strategy,
        start,
        end,
        initial_capital=args.initial_capital,
        contracts=args.contracts,
        costs=FuturesCostConfig(
            multiplier=args.contract_multiplier,
            commission_per_side=args.commission_per_side,
            tax_rate=args.tax_rate,
            slippage_points=args.slippage_points,
        ),
        source="臺灣期貨交易所逐筆 CSV",
    )

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(result["bars"]).to_csv(output / "bars.csv", index=False)
    pd.DataFrame(result["trades"]).to_csv(output / "trades.csv", index=False)
    pd.DataFrame(result["equity"]).to_csv(output / "equity.csv", index=False)
    (output / "summary.json").write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"已載入 {len(ticks):,} 筆逐筆成交，聚合為 "
        f"{len(canonical_bars):,} 根 1min K 棒。"
    )
    print_summary(result["summary"])
    print(f"\n報告位置：{output.resolve()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="台股分鐘線當沖回測 MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser("demo", help="產生合成資料並執行完整回測")
    add_backtest_arguments(demo)
    demo.set_defaults(func=run_demo, direction="both", output="output/demo")

    backtest = subparsers.add_parser("backtest", help="使用自己的 1 分 K CSV 回測")
    backtest.add_argument("--csv", required=True, help="CSV 路徑")
    backtest.add_argument("--symbol", help="CSV 無 symbol 欄時使用")
    add_backtest_arguments(backtest)
    backtest.set_defaults(func=run_backtest)

    futures = subparsers.add_parser(
        "futures-night",
        help="讀取期交所逐筆 CSV，使用與即時系統相同的 1 分 K 策略回測",
    )
    futures.add_argument("--csv", required=True, help="期交所 Daily_YYYY_MM_DD.csv")
    futures.add_argument("--product", default="TMF")
    futures.add_argument("--contract-month", required=True, help="例如 202609")
    futures.add_argument("--session-start", required=True, help="例如 2026-08-24 15:00")
    futures.add_argument("--session-end", required=True, help="例如 2026-08-25 05:00")
    futures.add_argument("--strategy", choices=("orb", "bnf"), default="orb")
    futures.add_argument("--output", default="output/futures-night")
    futures.add_argument("--initial-capital", type=float, default=100_000)
    futures.add_argument("--contracts", type=int, default=1)
    futures.add_argument("--contract-multiplier", type=float, default=10.0)
    futures.add_argument("--commission-per-side", type=float, default=10.0)
    futures.add_argument("--tax-rate", type=float, default=0.00002)
    futures.add_argument("--slippage-points", type=float, default=1.0)
    futures.set_defaults(func=run_futures_night)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)
