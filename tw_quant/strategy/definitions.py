from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

import pandas as pd


Direction = Literal["long", "short", "both"]


@dataclass(frozen=True)
class OpeningRangeBreakoutConfig:
    opening_range_minutes: int = 15
    volume_window: int = 5
    volume_multiplier: float = 1.2
    last_entry_time: time = time(12, 45)
    direction: Direction = "long"

    def __post_init__(self) -> None:
        if self.opening_range_minutes <= 0:
            raise ValueError("opening_range_minutes 必須大於 0")
        if self.volume_window <= 0:
            raise ValueError("volume_window 必須大於 0")
        if self.volume_multiplier < 0:
            raise ValueError("volume_multiplier 不可為負數")
        if self.direction not in ("long", "short", "both"):
            raise ValueError("direction 必須是 long、short 或 both")


class OpeningRangeBreakout:
    """開盤區間突破：訊號使用當根收盤資訊，撮合引擎於下一根開盤成交。"""

    def __init__(self, config: OpeningRangeBreakoutConfig | None = None):
        self.config = config or OpeningRangeBreakoutConfig()

    def generate_entries(self, day_bars: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=day_bars.index, dtype="int8")
        if day_bars.empty:
            return signals

        first_ts = day_bars["timestamp"].iloc[0]
        session_date = first_ts.date()
        tz = first_ts.tzinfo
        session_open = pd.Timestamp(datetime.combine(session_date, time(9, 0)), tz=tz)
        range_end = session_open + timedelta(minutes=self.config.opening_range_minutes)

        opening = day_bars.loc[
            (day_bars["timestamp"] >= session_open)
            & (day_bars["timestamp"] < range_end)
        ]
        if len(opening) < 2:
            return signals

        range_high = float(opening["high"].max())
        range_low = float(opening["low"].min())
        previous_close = day_bars["close"].shift(1)
        baseline_volume = (
            day_bars["volume"]
            .rolling(self.config.volume_window, min_periods=1)
            .mean()
            .shift(1)
        )
        local_time = day_bars["timestamp"].dt.time
        eligible = (
            (day_bars["timestamp"] >= range_end)
            & (local_time <= self.config.last_entry_time)
            & (day_bars["volume"] >= baseline_volume * self.config.volume_multiplier)
        )

        long_break = eligible & (day_bars["close"] > range_high) & (previous_close <= range_high)
        short_break = eligible & (day_bars["close"] < range_low) & (previous_close >= range_low)

        candidates: list[tuple[int, int]] = []
        if self.config.direction in ("long", "both"):
            candidates.extend((int(index), 1) for index in signals.index[long_break])
        if self.config.direction in ("short", "both"):
            candidates.extend((int(index), -1) for index in signals.index[short_break])
        if candidates:
            first_index, signal = min(candidates, key=lambda item: item[0])
            signals.loc[first_index] = signal
        return signals

@dataclass(frozen=True)
class BNFMeanReversionConfig:
    """Configurable BNF-style mean-reversion rules.

    BNF is used here as a transparent, testable adaptation rather than a claim
    that Takashi Kotegawa published one canonical mechanical formula.
    """

    mean_window: int = 20
    std_window: int = 20
    entry_z_score: float = 2.0
    exit_z_score: float = 0.5
    rsi_period: int = 14
    oversold_rsi: float = 30.0
    overbought_rsi: float = 70.0
    direction: Direction = "both"

    def __post_init__(self) -> None:
        if self.mean_window < 2 or self.std_window < 2:
            raise ValueError("mean_window 與 std_window 必須至少為 2")
        if self.entry_z_score <= 0:
            raise ValueError("entry_z_score 必須大於 0")
        if not 0 <= self.exit_z_score < self.entry_z_score:
            raise ValueError("exit_z_score 必須介於 0 與 entry_z_score 之間")
        if self.rsi_period < 2:
            raise ValueError("rsi_period 必須至少為 2")
        if not 0 <= self.oversold_rsi < self.overbought_rsi <= 100:
            raise ValueError("RSI 門檻必須滿足 0 <= oversold < overbought <= 100")
        if self.direction not in ("long", "short", "both"):
            raise ValueError("direction 必須是 long、short 或 both")


class BNFMeanReversion:
    """BNF-style mean reversion with z-score and RSI confirmation.

    Entries are confirmed from a closed bar and filled by the engine at the
    next bar's open. Mean-reversion exits follow the same next-open rule.
    """

    def __init__(self, config: BNFMeanReversionConfig | None = None):
        self.config = config or BNFMeanReversionConfig()

    def indicators(self, bars: pd.DataFrame) -> pd.DataFrame:
        close = bars["close"].astype(float)
        mean = close.rolling(
            self.config.mean_window, min_periods=self.config.mean_window
        ).mean()
        std = close.rolling(
            self.config.std_window, min_periods=self.config.std_window
        ).std(ddof=0)
        z_score = (close - mean) / std.replace(0, float("nan"))

        delta = close.diff()
        gain = delta.clip(lower=0).rolling(
            self.config.rsi_period, min_periods=self.config.rsi_period
        ).mean()
        loss = (-delta.clip(upper=0)).rolling(
            self.config.rsi_period, min_periods=self.config.rsi_period
        ).mean()
        denominator = gain + loss
        rsi = 100 * gain / denominator.replace(0, float("nan"))
        rsi = rsi.mask((gain == 0) & (loss == 0), 50.0)

        return pd.DataFrame(
            {"mean": mean, "std": std, "z_score": z_score, "rsi": rsi},
            index=bars.index,
        )

    def generate_entries(self, day_bars: pd.DataFrame) -> pd.Series:
        signals = pd.Series(0, index=day_bars.index, dtype="int8")
        if day_bars.empty:
            return signals
        indicators = self.indicators(day_bars)
        z_score = indicators["z_score"]
        previous_z = z_score.shift(1).fillna(0.0)
        rsi = indicators["rsi"]

        long_entry = (
            (z_score <= -self.config.entry_z_score)
            & (previous_z > -self.config.entry_z_score)
            & (rsi <= self.config.oversold_rsi)
        )
        short_entry = (
            (z_score >= self.config.entry_z_score)
            & (previous_z < self.config.entry_z_score)
            & (rsi >= self.config.overbought_rsi)
        )
        if self.config.direction in ("long", "both"):
            signals.loc[long_entry] = 1
        if self.config.direction in ("short", "both"):
            signals.loc[short_entry] = -1
        return signals

    def generate_exits(self, day_bars: pd.DataFrame) -> pd.DataFrame:
        indicators = self.indicators(day_bars)
        z_score = indicators["z_score"]
        return pd.DataFrame(
            {
                "long": z_score >= -self.config.exit_z_score,
                "short": z_score <= self.config.exit_z_score,
            },
            index=day_bars.index,
        ).fillna(False)
