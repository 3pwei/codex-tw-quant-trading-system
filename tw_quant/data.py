from __future__ import annotations

from datetime import time
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = ("timestamp", "symbol", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")
TAIPEI_TZ = "Asia/Taipei"


class BarDataError(ValueError):
    pass


def load_bars(path: str | Path, symbol: str | None = None) -> pd.DataFrame:
    """讀取 1 分 K CSV；timestamp 若無時區，視為台北時間。"""

    bars = pd.read_csv(path)
    bars.columns = [str(column).strip().lower() for column in bars.columns]

    if "symbol" not in bars.columns and symbol:
        bars["symbol"] = str(symbol)

    missing = sorted(set(REQUIRED_COLUMNS) - set(bars.columns))
    if missing:
        raise BarDataError(f"CSV 缺少欄位：{', '.join(missing)}")

    try:
        timestamps = pd.to_datetime(bars["timestamp"], errors="raise")
    except Exception as exc:
        raise BarDataError("timestamp 無法解析") from exc

    if timestamps.dt.tz is None:
        timestamps = timestamps.dt.tz_localize(
            TAIPEI_TZ,
            ambiguous="raise",
            nonexistent="raise",
        )
    else:
        timestamps = timestamps.dt.tz_convert(TAIPEI_TZ)
    bars["timestamp"] = timestamps

    for column in (*PRICE_COLUMNS, "volume"):
        bars[column] = pd.to_numeric(bars[column], errors="coerce")

    bars["symbol"] = bars["symbol"].astype(str).str.strip()
    bars = bars.loc[:, REQUIRED_COLUMNS].sort_values(["timestamp", "symbol"])
    bars = bars.reset_index(drop=True)
    validate_bars(bars)
    return bars


def regular_session_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """只保留台股普通交易時段。13:30 K 棒是否存在取決於資料商定義。"""

    local_time = bars["timestamp"].dt.time
    mask = (local_time >= time(9, 0)) & (local_time <= time(13, 30))
    result = bars.loc[mask].copy().reset_index(drop=True)
    if result.empty:
        raise BarDataError("找不到 09:00–13:30 的普通交易時段資料")
    return result


def validate_bars(bars: pd.DataFrame) -> None:
    missing = sorted(set(REQUIRED_COLUMNS) - set(bars.columns))
    if missing:
        raise BarDataError(f"資料缺少欄位：{', '.join(missing)}")
    if bars.empty:
        raise BarDataError("K 棒資料不可為空")
    if bars[list(REQUIRED_COLUMNS)].isna().any().any():
        bad = bars.columns[bars.isna().any()].tolist()
        raise BarDataError(f"資料含空值：{', '.join(bad)}")
    if (bars[list(PRICE_COLUMNS)] <= 0).any().any():
        raise BarDataError("OHLC 價格必須大於 0")
    if (bars["volume"] < 0).any():
        raise BarDataError("volume 不可為負數")
    if (bars["high"] < bars[list(("open", "close", "low"))].max(axis=1)).any():
        raise BarDataError("high 小於同根 K 棒的其他價格")
    if (bars["low"] > bars[list(("open", "close", "high"))].min(axis=1)).any():
        raise BarDataError("low 大於同根 K 棒的其他價格")
    if bars.duplicated(["timestamp", "symbol"]).any():
        raise BarDataError("timestamp + symbol 不可重複")

