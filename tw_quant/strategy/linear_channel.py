from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str
    confirmed_at: int


@dataclass(frozen=True)
class DowChannel:
    direction: str
    first_anchor: Pivot
    second_anchor: Pivot
    slope: float
    opposite_offset: float
    confirmed_at: int

    def levels(self, index: int) -> tuple[float, float, float]:
        anchor = self.second_anchor.price + self.slope * (
            index - self.second_anchor.index
        )
        if self.direction == "up":
            lower, upper = anchor, anchor + self.opposite_offset
        else:
            upper, lower = anchor, anchor - self.opposite_offset
        return upper, (upper + lower) / 2, lower


def _atr(bars: pd.DataFrame, period: int) -> pd.Series:
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period, min_periods=period).mean()


def _confirmed_pivot(
    bars: pd.DataFrame,
    current: int,
    confirmation_bars: int,
    atr: pd.Series,
    reversal_atr: float,
    last_pivot: Pivot | None,
    minimum_distance: int,
) -> Pivot | None:
    candidate = current - confirmation_bars
    if candidate < confirmation_bars or pd.isna(atr.iloc[candidate]):
        return None
    start = candidate - confirmation_bars
    sample = bars.iloc[start:current + 1]
    high = float(bars.iloc[candidate]["high"])
    low = float(bars.iloc[candidate]["low"])
    is_high = high >= float(sample["high"].max())
    is_low = low <= float(sample["low"].min())
    if not is_high and not is_low:
        return None

    choices: list[Pivot] = []
    threshold = float(atr.iloc[candidate]) * reversal_atr
    if is_high:
        choices.append(Pivot(candidate, high, "high", current))
    if is_low:
        choices.append(Pivot(candidate, low, "low", current))
    if last_pivot is None:
        return choices[0]
    if candidate - last_pivot.index < minimum_distance:
        return None
    opposite = [pivot for pivot in choices if pivot.kind != last_pivot.kind]
    if not opposite:
        return None
    pivot = max(opposite, key=lambda item: abs(item.price - last_pivot.price))
    if abs(pivot.price - last_pivot.price) < threshold:
        return None
    return pivot


def _build_channel(
    pivots: list[Pivot], current: int, minimum_bars: int
) -> DowChannel | None:
    highs = [pivot for pivot in pivots if pivot.kind == "high"]
    lows = [pivot for pivot in pivots if pivot.kind == "low"]
    if len(highs) < 2 or len(lows) < 2:
        return None
    high1, high2 = highs[-2:]
    low1, low2 = lows[-2:]

    if low2.price > low1.price and high2.price > high1.price:
        first, second, direction = low1, low2, "up"
    elif high2.price < high1.price and low2.price < low1.price:
        first, second, direction = high1, high2, "down"
    else:
        return None
    if second.index - first.index < minimum_bars:
        return None
    slope = (second.price - first.price) / (second.index - first.index)
    opposite = highs if direction == "up" else lows
    candidates = [
        pivot for pivot in opposite if first.index <= pivot.index <= current
    ]
    if not candidates:
        return None
    residuals = [
        pivot.price
        - (second.price + slope * (pivot.index - second.index))
        for pivot in candidates
    ]
    offset = max(residuals) if direction == "up" else abs(min(residuals))
    if offset <= 0:
        return None
    return DowChannel(
        direction=direction,
        first_anchor=first,
        second_anchor=second,
        slope=slope,
        opposite_offset=offset,
        confirmed_at=current,
    )


def detect_linear_channels(
    bars: pd.DataFrame,
    *,
    atr_period: int,
    pivot_reversal_atr: float,
    confirmation_bars: int,
    minimum_pivot_distance: int,
    minimum_channel_bars: int,
    invalidation_bars: int,
) -> pd.DataFrame:
    """Detect locked Dow Theory channels using only confirmed swing points."""
    columns = [
        "upper", "center", "lower", "slope", "direction", "channel_start",
        "anchor_1", "anchor_2",
    ]
    output = pd.DataFrame(index=bars.index, columns=columns)
    atr = _atr(bars, atr_period)
    pivots: list[Pivot] = []
    active: DowChannel | None = None
    blocked_signature: tuple[str, int, int] | None = None
    invalid_count = 0

    for current in range(len(bars)):
        pivot = _confirmed_pivot(
            bars,
            current,
            confirmation_bars,
            atr,
            pivot_reversal_atr,
            pivots[-1] if pivots else None,
            minimum_pivot_distance,
        )
        if pivot is not None:
            pivots.append(pivot)
        if active is None:
            candidate = _build_channel(pivots, current, minimum_channel_bars)
            if candidate is not None:
                signature = (
                    candidate.direction,
                    candidate.first_anchor.index,
                    candidate.second_anchor.index,
                )
                if signature != blocked_signature:
                    active = candidate
        if active is None:
            continue

        upper, center, lower = active.levels(current)
        output.loc[bars.index[current]] = [
            upper,
            center,
            lower,
            active.slope,
            active.direction,
            active.confirmed_at,
            active.first_anchor.index,
            active.second_anchor.index,
        ]
        close = float(bars.iloc[current]["close"])
        invalid = close < lower if active.direction == "up" else close > upper
        invalid_count = invalid_count + 1 if invalid else 0
        if invalid_count >= invalidation_bars:
            blocked_signature = (
                active.direction,
                active.first_anchor.index,
                active.second_anchor.index,
            )
            active = None
            invalid_count = 0

    return output


def serialize_channel_overlay(
    bars: pd.DataFrame, channels: pd.DataFrame
) -> dict[str, object]:
    points = []
    serialized_channels: set[str] = set()
    for index, row in channels.dropna(subset=["upper", "center", "lower"]).iterrows():
        channel_start = int(row["channel_start"])
        start_time = bars.iloc[channel_start]["timestamp"].isoformat(
            timespec="milliseconds"
        )
        channel_id = f"{start_time}:{row['direction']}"
        if channel_id not in serialized_channels:
            anchor = int(row["anchor_1"])
            distance = int(index) - anchor
            points.append({
                "time": bars.iloc[anchor]["timestamp"].isoformat(
                    timespec="milliseconds"
                ),
                "upper": round(float(row["upper"]) - float(row["slope"]) * distance, 6),
                "center": round(float(row["center"]) - float(row["slope"]) * distance, 6),
                "lower": round(float(row["lower"]) - float(row["slope"]) * distance, 6),
                "slope": round(float(row["slope"]), 6),
                "direction": str(row["direction"]),
                "channel_id": channel_id,
                "channel_start": channel_start,
                "anchor_1": anchor,
                "anchor_2": int(row["anchor_2"]),
                "anchor": True,
            })
            serialized_channels.add(channel_id)
        points.append({
            "time": bars.loc[index, "timestamp"].isoformat(timespec="milliseconds"),
            "upper": round(float(row["upper"]), 6),
            "center": round(float(row["center"]), 6),
            "lower": round(float(row["lower"]), 6),
            "slope": round(float(row["slope"]), 6),
            "direction": str(row["direction"]),
            "channel_id": channel_id,
            "channel_start": channel_start,
            "anchor_1": int(row["anchor_1"]),
            "anchor_2": int(row["anchor_2"]),
        })
    return {"type": "linear_channel", "model": "dow_theory", "points": points}
