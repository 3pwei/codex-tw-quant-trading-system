from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LinearChannelPoint:
    upper: float
    center: float
    lower: float
    score: float
    r_squared: float
    lookback: int
    slope: float


def _fit_candidate(window: pd.DataFrame, quantile: float) -> LinearChannelPoint | None:
    size = len(window)
    x = np.arange(size, dtype=float)
    close = window["close"].to_numpy(dtype=float)
    high = window["high"].to_numpy(dtype=float)
    low = window["low"].to_numpy(dtype=float)
    slope, intercept = np.polyfit(x, close, 1)
    fitted = intercept + slope * x
    residual_sum = float(np.square(close - fitted).sum())
    total_sum = float(np.square(close - close.mean()).sum())
    r_squared = 1.0 - residual_sum / total_sum if total_sum > 0 else 0.0

    upper_distance = float(np.quantile(high - fitted, quantile))
    lower_distance = float(np.quantile(fitted - low, quantile))
    if upper_distance <= 0 or lower_distance <= 0:
        return None
    contained = float(
        np.mean((high <= fitted + upper_distance) & (low >= fitted - lower_distance))
    )
    typical_price = max(abs(float(close.mean())), 1e-9)
    relative_width = (upper_distance + lower_distance) / typical_price
    # Prefer a straight, well-contained and sufficiently long channel, while
    # mildly penalising channels so wide that they have little trading value.
    score = (
        0.60 * max(0.0, r_squared)
        + 0.30 * contained
        + 0.10 * min(size / 100.0, 1.0)
        - min(relative_width, 0.10)
    )
    projected = intercept + slope * size
    return LinearChannelPoint(
        upper=projected + upper_distance,
        center=projected,
        lower=projected - lower_distance,
        score=score,
        r_squared=r_squared,
        lookback=size,
        slope=float(slope),
    )


def detect_linear_channels(
    bars: pd.DataFrame,
    *,
    minimum_lookback: int,
    maximum_lookback: int,
    lookback_step: int,
    boundary_quantile: float,
    minimum_r_squared: float,
    minimum_containment: float,
) -> pd.DataFrame:
    """Return the best as-of channel at every bar without looking ahead.

    The row at position ``i`` is fitted exclusively from bars before ``i`` and
    projected one step forward. This makes the output safe to share between
    historical backtests, replay and live analysis.
    """
    columns = ["upper", "center", "lower", "score", "r_squared", "lookback", "slope"]
    output = pd.DataFrame(index=bars.index, columns=columns, dtype=float)
    candidates = list(range(minimum_lookback, maximum_lookback + 1, lookback_step))
    if maximum_lookback not in candidates:
        candidates.append(maximum_lookback)

    for position in range(minimum_lookback, len(bars)):
        best: LinearChannelPoint | None = None
        for lookback in candidates:
            if lookback > position:
                break
            window = bars.iloc[position - lookback:position]
            candidate = _fit_candidate(window, boundary_quantile)
            if candidate is None:
                continue
            fitted_x = np.arange(lookback, dtype=float)
            center = candidate.center - candidate.slope * (lookback - fitted_x)
            high = window["high"].to_numpy(dtype=float)
            low = window["low"].to_numpy(dtype=float)
            upper_distance = candidate.upper - candidate.center
            lower_distance = candidate.center - candidate.lower
            containment = float(
                np.mean((high <= center + upper_distance) & (low >= center - lower_distance))
            )
            if (
                candidate.r_squared < minimum_r_squared
                or containment < minimum_containment
            ):
                continue
            if best is None or candidate.score > best.score:
                best = candidate
        if best is not None:
            output.iloc[position] = [
                best.upper, best.center, best.lower, best.score,
                best.r_squared, best.lookback, best.slope,
            ]
    return output


def serialize_channel_overlay(
    bars: pd.DataFrame, channels: pd.DataFrame
) -> dict[str, object]:
    points = []
    for index, row in channels.dropna(subset=["upper", "center", "lower"]).iterrows():
        points.append({
            "time": bars.loc[index, "timestamp"].isoformat(timespec="milliseconds"),
            "upper": round(float(row["upper"]), 6),
            "center": round(float(row["center"]), 6),
            "lower": round(float(row["lower"]), 6),
            "score": round(float(row["score"]), 6),
            "r_squared": round(float(row["r_squared"]), 6),
            "lookback": int(row["lookback"]),
            "slope": round(float(row["slope"]), 6),
        })
    return {"type": "linear_channel", "points": points}
