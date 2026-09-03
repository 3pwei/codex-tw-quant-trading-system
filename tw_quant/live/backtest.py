from __future__ import annotations

from datetime import date

import pandas as pd

from ..futures_costs import FuturesCostConfig
from ..metrics import build_equity_curve, calculate_summary
from .models import KBar
from .strategy_analysis import SUPPORTED_STRATEGIES, analyze_live_strategies


MAX_BACKTEST_DAYS = 31
INITIAL_CAPITAL = 100_000.0


def validate_date_range(start: date, end: date) -> None:
    if start > end:
        raise ValueError("開始日期不可晚於結束日期")
    if (end - start).days + 1 > MAX_BACKTEST_DAYS:
        raise ValueError(f"回測區間最多 {MAX_BACKTEST_DAYS} 天")


def _trade_from_signals(
    entry: dict[str, object],
    exit_signal: dict[str, object],
    bars: list[KBar],
    costs: FuturesCostConfig,
    initial_capital: float,
    contracts: int,
) -> dict[str, object]:
    direction = 1 if entry["direction"] == "long" else -1
    raw_entry = float(entry["price"])
    raw_exit = float(exit_signal["price"])
    entry_price = raw_entry + costs.slippage_points * direction
    exit_price = raw_exit - costs.slippage_points * direction
    entry_time = pd.Timestamp(str(entry["time"]))
    exit_time = pd.Timestamp(str(exit_signal["time"]))
    segment = [
        bar for bar in bars
        if entry_time <= pd.Timestamp(bar.time) <= exit_time
        and bar.contract == entry["contract"]
    ]
    favorable = 0.0
    adverse = 0.0
    for bar in segment:
        favorable = max(
            favorable,
            (bar.high - entry_price) if direction == 1 else (entry_price - bar.low),
        )
        adverse = min(
            adverse,
            (bar.low - entry_price) if direction == 1 else (entry_price - bar.high),
        )

    entry_commission, entry_tax = costs.side_cost(entry_price, contracts)
    exit_commission, exit_tax = costs.side_cost(exit_price, contracts)
    commission = entry_commission + exit_commission
    tax = entry_tax + exit_tax
    total_cost = commission + tax
    gross_pnl = (exit_price - entry_price) * direction * costs.multiplier * contracts
    net_pnl = gross_pnl - total_cost
    return {
        "strategy": entry["strategy"],
        "contract": entry["contract"],
        "trading_date": entry["trading_date"],
        "direction": entry["direction"],
        "entry_time": entry_time.to_pydatetime(),
        "exit_time": exit_time.to_pydatetime(),
        "quantity": contracts,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "stop_loss_price": float(entry["stop_loss_price"]),
        "take_profit_price": float(entry["take_profit_price"]),
        "gross_pnl": gross_pnl,
        "commission": commission,
        "tax": tax,
        "total_cost": total_cost,
        "net_pnl": net_pnl,
        "return_pct": net_pnl / initial_capital * 100,
        "holding_minutes": max(1.0, (exit_time - entry_time).total_seconds() / 60),
        "mfe": favorable * costs.multiplier * contracts,
        "mae": adverse * costs.multiplier * contracts,
        "exit_reason": exit_signal["reason"],
    }


def run_live_strategy_backtest(
    bars: list[KBar],
    strategy: str,
    start: date,
    end: date,
    *,
    initial_capital: float = INITIAL_CAPITAL,
    contracts: int = 1,
    costs: FuturesCostConfig | None = None,
) -> dict[str, object]:
    validate_date_range(start, end)
    strategy = strategy.lower()
    if strategy not in SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported strategy: {strategy}")
    closed = [bar for bar in bars if bar.status == "closed"]
    if not closed:
        raise ValueError("所選區間沒有可用的已收盤 K 棒")

    analysis = analyze_live_strategies(
        closed, [strategy], force_close_last=True
    )["strategies"][0]
    signals = analysis["signals"]
    cost_config = costs or FuturesCostConfig()
    entries: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    for signal in signals:
        if signal["event"] == "entry":
            entries.append(signal)
        elif entries:
            trades.append(
                _trade_from_signals(
                    entries.pop(0), signal, closed, cost_config,
                    initial_capital, contracts,
                )
            )

    bar_frame = pd.DataFrame(
        [{"timestamp": bar.time, "close": bar.close} for bar in closed]
    )
    trade_frame = pd.DataFrame(trades)
    equity = build_equity_curve(trade_frame, initial_capital, bar_frame)
    summary = calculate_summary(trade_frame, equity, initial_capital)

    serialized_trades = []
    for trade in trades:
        item = dict(trade)
        item["entry_time"] = trade["entry_time"].isoformat(timespec="milliseconds")
        item["exit_time"] = trade["exit_time"].isoformat(timespec="milliseconds")
        serialized_trades.append(item)

    return {
        "metadata": {
            "symbol": closed[0].symbol,
            "display_name": "微型臺指期貨",
            "strategy": analysis["name"],
            "strategy_key": strategy,
            "interval": "1 分鐘",
            "date_range": f"{start.isoformat()} ～ {end.isoformat()}",
            "is_synthetic": False,
            "source": "即時行情 SQLite（已收盤 1 分 K）",
        },
        "config": {
            "initial_capital": initial_capital,
            "quantity": contracts,
            "quantity_unit": "口",
            "opening_range_minutes": 15,
            "bar_minutes": 1,
            "stop_loss_pct": float(analysis["parameters"]["stop_loss_pct"]),
            "take_profit_pct": float(analysis["parameters"]["take_profit_pct"]),
            "force_exit_time": "每交易時段結束",
            "commission_rate": 0,
            "commission_per_side": cost_config.commission_per_side,
            "sell_tax_rate": cost_config.tax_rate,
            "slippage_bps": 0,
            "slippage_points": cost_config.slippage_points,
            "contract_multiplier": cost_config.multiplier,
        },
        "summary": summary,
        "bars": [
            {
                "timestamp": bar.time.isoformat(timespec="milliseconds"),
                "open": bar.open, "high": bar.high, "low": bar.low,
                "close": bar.close, "volume": bar.volume,
                "contract": bar.contract, "session": bar.session,
                "trading_date": bar.trading_date.isoformat(),
            }
            for bar in closed
        ],
        "trades": serialized_trades,
        "equity": [
            {
                **row,
                "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
            }
            for row in equity.to_dict(orient="records")
        ],
    }
