from __future__ import annotations

from pathlib import Path

import pandas as pd

from .futures_costs import FuturesCostConfig
from .live.models import KBar
from .live.sessions import DEFAULT_CALENDAR, TradingCalendar, classify_tmf_session

__all__ = [
    "FuturesCostConfig",
    "load_taifex_ticks",
    "taifex_bars_to_kbars",
    "ticks_to_bars",
]


def load_taifex_ticks(
    path: str | Path,
    *,
    product: str,
    contract_month: str,
    session_start: str | pd.Timestamp,
    session_end: str | pd.Timestamp,
) -> pd.DataFrame:
    """讀取期交所逐筆成交 CSV，並篩選指定商品、月份與時段。"""
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
    interval: str = "1min",
    session_start: str | pd.Timestamp,
    symbol: str = "TMF",
) -> pd.DataFrame:
    """將期交所逐筆資料聚合為 OHLCV；策略回測固定傳入 1min。"""
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


def taifex_bars_to_kbars(
    bars: pd.DataFrame,
    *,
    symbol: str,
    contract: str,
    interval: str = "1min",
    calendar: TradingCalendar = DEFAULT_CALENDAR,
) -> list[KBar]:
    """Convert imported TAIFEX bars into the canonical live/backtest KBar model."""
    duration = pd.Timedelta(interval)
    result: list[KBar] = []
    for row in bars.itertuples(index=False):
        timestamp = pd.Timestamp(row.timestamp).to_pydatetime()
        if timestamp.tzinfo is None:
            raise ValueError("期交所 K 棒時間必須包含時區")
        session, trading_date = classify_tmf_session(timestamp, calendar)
        last_tick_time = (
            pd.Timestamp(timestamp) + duration - pd.Timedelta(microseconds=1)
        ).to_pydatetime()
        result.append(
            KBar(
                symbol=symbol,
                contract=contract,
                time=timestamp,
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(row.volume),
                status="closed",
                session=session,
                trading_date=trading_date,
                first_tick_time=timestamp,
                last_tick_time=last_tick_time,
                exchange_time=last_tick_time,
                received_time=last_tick_time,
                latency_ms=0,
            )
        )
    return result
