from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd

from .config import BacktestConfig, CostConfig
from .costs import TaiwanStockCostModel
from .data import regular_session_bars, validate_bars
from .metrics import build_equity_curve, calculate_summary


class EntryStrategy(Protocol):
    def generate_entries(self, day_bars: pd.DataFrame) -> pd.Series: ...


@dataclass(frozen=True)
class Trade:
    symbol: str
    direction: str
    entry_time: datetime
    exit_time: datetime
    quantity: int
    entry_price: float
    exit_price: float
    gross_pnl: float
    commission: float
    tax: float
    total_cost: float
    net_pnl: float
    return_pct: float
    holding_minutes: float
    mfe: float
    mae: float
    exit_reason: str


@dataclass(frozen=True)
class BacktestResult:
    trades: pd.DataFrame
    equity: pd.DataFrame
    summary: dict[str, float | int | None]


class BacktestEngine:
    def __init__(
        self,
        strategy: EntryStrategy,
        backtest_config: BacktestConfig | None = None,
        cost_config: CostConfig | None = None,
    ):
        self.strategy = strategy
        self.config = backtest_config or BacktestConfig()
        self.cost_model = TaiwanStockCostModel(cost_config or CostConfig())

    def run(self, bars: pd.DataFrame) -> BacktestResult:
        validate_bars(bars)
        bars = regular_session_bars(bars).sort_values("timestamp").reset_index(drop=True)
        symbols = bars["symbol"].unique().tolist()
        if len(symbols) != 1:
            raise ValueError("MVP 每次只回測一個 symbol；請將多標的拆開執行")

        local_dates = bars["timestamp"].dt.date
        trades: list[Trade] = []
        realized_pnl = 0.0
        for _, day_bars in bars.groupby(local_dates, sort=True):
            day_bars = day_bars.reset_index(drop=True)
            day_trades = self._run_day(day_bars, realized_pnl)
            trades.extend(day_trades)
            realized_pnl += sum(trade.net_pnl for trade in day_trades)

        columns = list(Trade.__dataclass_fields__)
        trade_frame = pd.DataFrame([asdict(trade) for trade in trades], columns=columns)
        equity = build_equity_curve(trade_frame, self.config.initial_capital, bars)
        summary = calculate_summary(trade_frame, equity, self.config.initial_capital)
        return BacktestResult(trades=trade_frame, equity=equity, summary=summary)

    def _run_day(self, bars: pd.DataFrame, realized_pnl_before_day: float) -> list[Trade]:
        signals = self.strategy.generate_entries(bars).reindex(bars.index, fill_value=0)
        trades: list[Trade] = []
        pending_signal = 0
        position = 0
        entry_price = 0.0
        entry_time = None
        entry_cost = None
        stop_price = 0.0
        target_price = 0.0
        mfe_per_share = 0.0
        mae_per_share = 0.0

        for index, bar in bars.iterrows():
            timestamp = bar["timestamp"]
            current_time = timestamp.time()

            if (
                position == 0
                and pending_signal != 0
                and current_time < self.config.force_exit_time
                and len(trades) < self.config.max_trades_per_day
            ):
                if pending_signal == -1 and not self.config.allow_short:
                    pending_signal = 0
                else:
                    entry_side = "buy" if pending_signal == 1 else "sell"
                    candidate_price = self.cost_model.fill_price(float(bar["open"]), entry_side)
                    candidate_cost = self.cost_model.order_cost(
                        candidate_price,
                        self.config.quantity,
                        entry_side,
                    )
                    available_equity = (
                        self.config.initial_capital
                        + realized_pnl_before_day
                        + sum(trade.net_pnl for trade in trades)
                    )
                    required_cash = candidate_cost.notional + candidate_cost.total
                    if pending_signal == -1 or required_cash <= available_equity:
                        position = pending_signal
                        entry_price = candidate_price
                        entry_time = timestamp
                        entry_cost = candidate_cost
                        if position == 1:
                            stop_price = entry_price * (1 - self.config.stop_loss_pct)
                            target_price = entry_price * (1 + self.config.take_profit_pct)
                        else:
                            stop_price = entry_price * (1 + self.config.stop_loss_pct)
                            target_price = entry_price * (1 - self.config.take_profit_pct)
                        mfe_per_share = 0.0
                        mae_per_share = 0.0
                    pending_signal = 0

            if position != 0:
                if current_time >= self.config.force_exit_time:
                    raw_exit, reason = float(bar["open"]), "force_exit"
                else:
                    mfe_per_share, mae_per_share = self._update_excursions(
                        position,
                        entry_price,
                        float(bar["high"]),
                        float(bar["low"]),
                        mfe_per_share,
                        mae_per_share,
                    )
                    raw_exit, reason = self._exit_decision(
                        position,
                        bar,
                        stop_price,
                        target_price,
                    )

                if raw_exit is not None:
                    trade = self._close_trade(
                        symbol=str(bar["symbol"]),
                        position=position,
                        entry_time=entry_time,
                        exit_time=timestamp,
                        entry_price=entry_price,
                        raw_exit_price=raw_exit,
                        entry_cost=entry_cost,
                        mfe_per_share=mfe_per_share,
                        mae_per_share=mae_per_share,
                        reason=reason,
                    )
                    trades.append(trade)
                    position = 0
                    entry_time = None
                    entry_cost = None

            if position == 0 and pending_signal == 0 and len(trades) < self.config.max_trades_per_day:
                signal = int(signals.loc[index])
                if signal in (-1, 1) and current_time < self.config.force_exit_time:
                    pending_signal = signal

        if position != 0:
            last_bar = bars.iloc[-1]
            trade = self._close_trade(
                symbol=str(last_bar["symbol"]),
                position=position,
                entry_time=entry_time,
                exit_time=last_bar["timestamp"],
                entry_price=entry_price,
                raw_exit_price=float(last_bar["close"]),
                entry_cost=entry_cost,
                mfe_per_share=mfe_per_share,
                mae_per_share=mae_per_share,
                reason="end_of_data",
            )
            trades.append(trade)
        return trades

    def _exit_decision(
        self,
        position: int,
        bar: pd.Series,
        stop_price: float,
        target_price: float,
    ) -> tuple[float | None, str]:
        open_price = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])

        if position == 1:
            stop_hit = low <= stop_price
            target_hit = high >= target_price
            if stop_hit:  # 同根同時觸發時採保守的停損優先。
                return min(open_price, stop_price), "stop_loss"
            if target_hit:
                return max(open_price, target_price), "take_profit"
        else:
            stop_hit = high >= stop_price
            target_hit = low <= target_price
            if stop_hit:
                return max(open_price, stop_price), "stop_loss"
            if target_hit:
                return min(open_price, target_price), "take_profit"
        return None, ""

    @staticmethod
    def _update_excursions(
        position: int,
        entry_price: float,
        high: float,
        low: float,
        current_mfe: float,
        current_mae: float,
    ) -> tuple[float, float]:
        if position == 1:
            favorable = high - entry_price
            adverse = low - entry_price
        else:
            favorable = entry_price - low
            adverse = entry_price - high
        return max(current_mfe, favorable), min(current_mae, adverse)

    def _close_trade(
        self,
        *,
        symbol: str,
        position: int,
        entry_time,
        exit_time,
        entry_price: float,
        raw_exit_price: float,
        entry_cost,
        mfe_per_share: float,
        mae_per_share: float,
        reason: str,
    ) -> Trade:
        exit_side = "sell" if position == 1 else "buy"
        exit_price = self.cost_model.fill_price(raw_exit_price, exit_side)
        exit_cost = self.cost_model.order_cost(exit_price, self.config.quantity, exit_side)
        multiplier = 1 if position == 1 else -1
        gross_pnl = (exit_price - entry_price) * self.config.quantity * multiplier
        commission = entry_cost.commission + exit_cost.commission
        tax = entry_cost.tax + exit_cost.tax
        total_cost = commission + tax
        net_pnl = gross_pnl - total_cost
        holding_minutes = (exit_time - entry_time).total_seconds() / 60
        capital_at_risk = entry_price * self.config.quantity
        return Trade(
            symbol=symbol,
            direction="long" if position == 1 else "short",
            entry_time=entry_time,
            exit_time=exit_time,
            quantity=self.config.quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            gross_pnl=gross_pnl,
            commission=commission,
            tax=tax,
            total_cost=total_cost,
            net_pnl=net_pnl,
            return_pct=net_pnl / capital_at_risk * 100,
            holding_minutes=holding_minutes,
            mfe=mfe_per_share * self.config.quantity,
            mae=mae_per_share * self.config.quantity,
            exit_reason=reason,
        )
