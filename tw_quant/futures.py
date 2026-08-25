from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class FuturesCostConfig:
    multiplier: float = 10.0
    commission_per_side: float = 10.0
    tax_rate: float = 0.00002
    slippage_points: float = 1.0

    def side_cost(self, price: float, contracts: int = 1) -> tuple[float, float]:
        commission = self.commission_per_side * contracts
        tax = round(price * self.multiplier * contracts * self.tax_rate)
        return commission, tax


def load_taifex_ticks(
    path: str | Path,
    *,
    product: str,
    contract_month: str,
    session_start: str | pd.Timestamp,
    session_end: str | pd.Timestamp,
) -> pd.DataFrame:
    """讀取期交所逐筆成交 CSV，並篩選指定商品、月份與夜盤區間。"""
    frame = pd.read_csv(path, encoding="cp950", skipinitialspace=True, dtype=str)
    frame.columns = [column.strip() for column in frame.columns]
    for column in frame.columns:
        frame[column] = frame[column].str.strip()

    ticks = frame.loc[
        (frame["商品代號"] == product)
        & (frame["到期月份(週別)"] == contract_month)
    ].copy()
    timestamps = pd.to_datetime(
        ticks["成交日期"] + ticks["成交時間"].str.zfill(6),
        format="%Y%m%d%H%M%S",
    ).dt.tz_localize("Asia/Taipei")
    ticks = ticks.assign(
        timestamp=timestamps,
        price=pd.to_numeric(ticks["成交價格"], errors="coerce"),
        # 官方欄位是買賣雙方合計口數，因此除以 2 還原成交口數。
        volume=pd.to_numeric(ticks["成交數量(B+S)"], errors="coerce") / 2,
    ).dropna(subset=["timestamp", "price", "volume"])

    start = pd.Timestamp(session_start, tz="Asia/Taipei")
    end = pd.Timestamp(session_end, tz="Asia/Taipei")
    return ticks.loc[
        (ticks["timestamp"] >= start) & (ticks["timestamp"] < end),
        ["timestamp", "price", "volume"],
    ].sort_values("timestamp").reset_index(drop=True)


def ticks_to_bars(
    ticks: pd.DataFrame,
    *,
    interval: str = "5min",
    session_start: str | pd.Timestamp,
    symbol: str = "TMF",
) -> pd.DataFrame:
    start = pd.Timestamp(session_start, tz="Asia/Taipei")
    bars = (
        ticks.set_index("timestamp")
        .resample(interval, origin=start, label="left", closed="left")
        .agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("volume", "sum"),
        )
        .dropna()
        .reset_index()
    )
    bars.insert(1, "symbol", symbol)
    return bars


def run_night_orb(
    bars: pd.DataFrame,
    *,
    initial_capital: float = 100_000,
    contracts: int = 1,
    opening_range_minutes: int = 15,
    volume_window: int = 5,
    volume_multiplier: float = 1.2,
    stop_loss_pct: float = 0.006,
    take_profit_pct: float = 0.012,
    last_entry_time: str | pd.Timestamp,
    costs: FuturesCostConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, float | int | None]]:
    """單一夜盤 ORB 回測；訊號於收盤產生，下一根 5 分 K 開盤成交。"""
    if bars.empty:
        raise ValueError("沒有可回測的 5 分 K")
    costs = costs or FuturesCostConfig()
    bar_minutes = int((bars["timestamp"].iloc[1] - bars["timestamp"].iloc[0]).total_seconds() / 60)
    opening_bars = max(1, opening_range_minutes // bar_minutes)
    if len(bars) <= opening_bars:
        raise ValueError("K 棒數不足以建立開盤區間")

    opening = bars.iloc[:opening_bars]
    range_high = float(opening["high"].max())
    range_low = float(opening["low"].min())
    previous_close = bars["close"].shift(1)
    baseline_volume = bars["volume"].rolling(volume_window, min_periods=1).mean().shift(1)
    eligible = (
        (bars.index >= opening_bars)
        & (bars["timestamp"] < pd.Timestamp(last_entry_time, tz="Asia/Taipei"))
        & (bars["volume"] >= baseline_volume * volume_multiplier)
    )
    long_break = eligible & (bars["close"] > range_high) & (previous_close <= range_high)
    short_break = eligible & (bars["close"] < range_low) & (previous_close >= range_low)
    candidates = [(int(index), 1) for index in bars.index[long_break]]
    candidates += [(int(index), -1) for index in bars.index[short_break]]
    if not candidates:
        raise ValueError("此夜盤沒有符合條件的 ORB 進場訊號")

    signal_index, direction = min(candidates, key=lambda item: item[0])
    entry_index = signal_index + 1
    if entry_index >= len(bars):
        raise ValueError("訊號出現在最後一根 K，無法於下一根成交")

    raw_entry = float(bars.iloc[entry_index]["open"])
    entry_price = raw_entry + (-costs.slippage_points if direction == 1 else costs.slippage_points)
    if direction == 1:
        # 買進需要向上滑價；賣出需要向下滑價。
        entry_price = raw_entry + costs.slippage_points
        stop_price = entry_price * (1 - stop_loss_pct)
        target_price = entry_price * (1 + take_profit_pct)
    else:
        entry_price = raw_entry - costs.slippage_points
        stop_price = entry_price * (1 + stop_loss_pct)
        target_price = entry_price * (1 - take_profit_pct)

    exit_index = len(bars) - 1
    raw_exit = float(bars.iloc[-1]["close"])
    exit_reason = "force_exit"
    max_favorable_points = 0.0
    max_adverse_points = 0.0

    for index in range(entry_index, len(bars)):
        bar = bars.iloc[index]
        if direction == 1:
            max_favorable_points = max(max_favorable_points, float(bar["high"]) - entry_price)
            max_adverse_points = min(max_adverse_points, float(bar["low"]) - entry_price)
            if float(bar["low"]) <= stop_price:
                raw_exit, exit_reason, exit_index = min(float(bar["open"]), stop_price), "stop_loss", index
                break
            if float(bar["high"]) >= target_price:
                raw_exit, exit_reason, exit_index = max(float(bar["open"]), target_price), "take_profit", index
                break
        else:
            max_favorable_points = max(max_favorable_points, entry_price - float(bar["low"]))
            max_adverse_points = min(max_adverse_points, entry_price - float(bar["high"]))
            if float(bar["high"]) >= stop_price:
                raw_exit, exit_reason, exit_index = max(float(bar["open"]), stop_price), "stop_loss", index
                break
            if float(bar["low"]) <= target_price:
                raw_exit, exit_reason, exit_index = min(float(bar["open"]), target_price), "take_profit", index
                break

    exit_price = raw_exit - costs.slippage_points if direction == 1 else raw_exit + costs.slippage_points
    entry_commission, entry_tax = costs.side_cost(entry_price, contracts)
    exit_commission, exit_tax = costs.side_cost(exit_price, contracts)
    commission = entry_commission + exit_commission
    tax = entry_tax + exit_tax
    total_cost = commission + tax
    gross_pnl = (exit_price - entry_price) * direction * costs.multiplier * contracts
    net_pnl = gross_pnl - total_cost
    entry_time = bars.iloc[entry_index]["timestamp"]
    exit_time = bars.iloc[exit_index]["timestamp"]

    trade = pd.DataFrame([{
        "direction": "long" if direction == 1 else "short",
        "entry_time": entry_time,
        "exit_time": exit_time,
        "quantity": contracts,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "gross_pnl": gross_pnl,
        "commission": commission,
        "tax": tax,
        "total_cost": total_cost,
        "net_pnl": net_pnl,
        "return_pct": net_pnl / initial_capital * 100,
        "holding_minutes": (exit_time - entry_time).total_seconds() / 60 + bar_minutes,
        "mfe": max_favorable_points * costs.multiplier * contracts,
        "mae": max_adverse_points * costs.multiplier * contracts,
        "exit_reason": exit_reason,
    }])

    points = []
    peak = initial_capital
    for index, bar in bars.iterrows():
        equity = initial_capital
        point_pnl = 0.0
        if entry_index <= index < exit_index:
            mark_pnl = (float(bar["close"]) - entry_price) * direction * costs.multiplier * contracts
            point_pnl = mark_pnl - entry_commission - entry_tax
            equity += point_pnl
        elif index >= exit_index:
            point_pnl = net_pnl
            equity += net_pnl
        peak = max(peak, equity)
        drawdown = equity - peak
        points.append({
            "timestamp": bar["timestamp"], "equity": equity, "net_pnl": point_pnl,
            "peak": peak, "drawdown": drawdown,
            "drawdown_pct": drawdown / peak * 100,
        })
    equity = pd.DataFrame(points)
    summary = {
        "initial_capital": initial_capital,
        "ending_equity": initial_capital + net_pnl,
        "trades": 1,
        "win_rate_pct": 100.0 if net_pnl > 0 else 0.0,
        "net_profit": net_pnl,
        "return_pct": net_pnl / initial_capital * 100,
        "gross_profit": max(net_pnl, 0.0),
        "gross_loss": min(net_pnl, 0.0),
        "profit_factor": None,
        "avg_trade": net_pnl,
        "avg_holding_minutes": float(trade.iloc[0]["holding_minutes"]),
        "total_cost": total_cost,
        "max_drawdown": abs(float(equity["drawdown"].min())),
        "max_drawdown_pct": abs(float(equity["drawdown_pct"].min())),
        "daily_sharpe": None,
    }
    return trade, equity, summary
