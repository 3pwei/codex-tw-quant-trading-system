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

