from __future__ import annotations

import math

import pandas as pd


def build_equity_curve(
    trades: pd.DataFrame,
    initial_capital: float,
    bars: pd.DataFrame,
) -> pd.DataFrame:
    start_time = bars["timestamp"].iloc[0]
    points = [{"timestamp": start_time, "equity": initial_capital, "net_pnl": 0.0}]
    running = initial_capital
    if not trades.empty:
        for row in trades.itertuples(index=False):
            running += float(row.net_pnl)
            points.append(
                {"timestamp": row.exit_time, "equity": running, "net_pnl": row.net_pnl}
            )
    result = pd.DataFrame(points)
    result["peak"] = result["equity"].cummax()
    result["drawdown"] = result["equity"] - result["peak"]
    result["drawdown_pct"] = result["drawdown"] / result["peak"] * 100
    return result


def calculate_summary(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    initial_capital: float,
) -> dict[str, float | int | None]:
    if trades.empty:
        return {
            "initial_capital": initial_capital,
            "ending_equity": initial_capital,
            "trades": 0,
            "win_rate_pct": 0.0,
            "net_profit": 0.0,
            "return_pct": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "profit_factor": None,
            "avg_trade": 0.0,
            "avg_holding_minutes": 0.0,
            "total_cost": 0.0,
            "max_drawdown": 0.0,
            "max_drawdown_pct": 0.0,
            "daily_sharpe": None,
        }

    wins = trades.loc[trades["net_pnl"] > 0, "net_pnl"]
    losses = trades.loc[trades["net_pnl"] < 0, "net_pnl"]
    gross_profit = float(wins.sum())
    gross_loss = float(losses.sum())
    profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else None
    ending_equity = float(equity["equity"].iloc[-1])

    exit_dates = pd.to_datetime(trades["exit_time"]).dt.date
    daily_pnl = trades.groupby(exit_dates)["net_pnl"].sum()
    daily_sharpe = None
    if len(daily_pnl) >= 2 and float(daily_pnl.std(ddof=1)) > 0:
        daily_return = daily_pnl / initial_capital
        daily_sharpe = float(daily_return.mean() / daily_return.std(ddof=1) * math.sqrt(252))

    return {
        "initial_capital": initial_capital,
        "ending_equity": ending_equity,
        "trades": int(len(trades)),
        "win_rate_pct": float((trades["net_pnl"] > 0).mean() * 100),
        "net_profit": float(trades["net_pnl"].sum()),
        "return_pct": (ending_equity / initial_capital - 1) * 100,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": profit_factor,
        "avg_trade": float(trades["net_pnl"].mean()),
        "avg_holding_minutes": float(trades["holding_minutes"].mean()),
        "total_cost": float(trades["total_cost"].sum()),
        "max_drawdown": abs(float(equity["drawdown"].min())),
        "max_drawdown_pct": abs(float(equity["drawdown_pct"].min())),
        "daily_sharpe": daily_sharpe,
    }
