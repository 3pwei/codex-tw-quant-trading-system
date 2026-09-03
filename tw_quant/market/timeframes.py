from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from typing import Iterable, Literal

from .models import KBar
from .sessions import TAIPEI


Timeframe = Literal["1m", "5m", "10m", "15m", "30m", "1h", "1d", "1w"]

SUPPORTED_TIMEFRAMES: tuple[Timeframe, ...] = (
    "1m", "5m", "10m", "15m", "30m", "1h", "1d", "1w",
)
TIMEFRAME_LABELS: dict[Timeframe, str] = {
    "1m": "1 分鐘",
    "5m": "5 分鐘",
    "10m": "10 分鐘",
    "15m": "15 分鐘",
    "30m": "30 分鐘",
    "1h": "1 小時",
    "1d": "日 K",
    "1w": "週 K",
}
TIMEFRAME_MINUTES: dict[Timeframe, int | None] = {
    "1m": 1,
    "5m": 5,
    "10m": 10,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "1d": None,
    "1w": None,
}


def validate_timeframe(value: str) -> Timeframe:
    normalized = value.lower().strip()
    if normalized not in SUPPORTED_TIMEFRAMES:
        supported = ", ".join(SUPPORTED_TIMEFRAMES)
        raise ValueError(f"unsupported interval: {value}; supported: {supported}")
    return normalized  # type: ignore[return-value]


def _session_anchor(bar: KBar) -> datetime:
    local = bar.time.astimezone(TAIPEI)
    if bar.session == "day":
        return datetime.combine(local.date(), time(8, 45), tzinfo=TAIPEI)
    anchor_date = local.date() if local.time() >= time(15) else local.date() - timedelta(days=1)
    return datetime.combine(anchor_date, time(15), tzinfo=TAIPEI)


def timeframe_bucket(bar: KBar, interval: str) -> tuple[object, ...]:
    """Return a contract-safe TMF bucket based on exchange/session time."""
    selected = validate_timeframe(interval)
    if selected == "1m":
        return bar.contract, bar.time.astimezone(TAIPEI).replace(second=0, microsecond=0)
    minutes = TIMEFRAME_MINUTES[selected]
    if minutes is not None:
        anchor = _session_anchor(bar)
        elapsed = int((bar.time.astimezone(TAIPEI) - anchor).total_seconds() // 60)
        bucket_time = anchor + timedelta(minutes=(elapsed // minutes) * minutes)
        return bar.contract, bar.session, bar.trading_date, bucket_time
    if selected == "1d":
        return bar.contract, bar.trading_date
    iso = bar.trading_date.isocalendar()
    return bar.contract, iso.year, iso.week


def _aggregate_group(group: list[KBar], interval: Timeframe) -> KBar:
    ordered = sorted(group, key=lambda bar: bar.time)
    first, last = ordered[0], ordered[-1]
    key = timeframe_bucket(first, interval)
    bucket_time = key[-1] if isinstance(key[-1], datetime) else first.time
    return KBar(
        symbol=first.symbol,
        contract=first.contract,
        time=bucket_time,
        open=first.open,
        high=max(bar.high for bar in ordered),
        low=min(bar.low for bar in ordered),
        close=last.close,
        volume=sum(bar.volume for bar in ordered),
        status="forming" if any(bar.status == "forming" for bar in ordered) else "closed",
        session=first.session,
        trading_date=first.trading_date,
        first_tick_time=min(bar.first_tick_time for bar in ordered),
        last_tick_time=max(bar.last_tick_time for bar in ordered),
        exchange_time=last.exchange_time,
        received_time=last.received_time,
        latency_ms=last.latency_ms,
        no_trade=all(bar.no_trade for bar in ordered),
    )


def aggregate_kbars(
    bars: Iterable[KBar], interval: str, limit: int | None = None
) -> list[KBar]:
    """Aggregate canonical 1-minute bars without crossing contracts or sessions."""
    selected = validate_timeframe(interval)
    ordered = sorted(bars, key=lambda bar: bar.time)
    if selected == "1m":
        result = ordered
    else:
        groups: OrderedDict[tuple[object, ...], list[KBar]] = OrderedDict()
        for bar in ordered:
            groups.setdefault(timeframe_bucket(bar, selected), []).append(bar)
        result = [_aggregate_group(group, selected) for group in groups.values()]
    return result[-limit:] if limit is not None else result


def source_bar_limit(interval: str, requested: int, history_limit: int) -> int:
    """Estimate source rows while respecting the configured retention ceiling."""
    selected = validate_timeframe(interval)
    minutes = TIMEFRAME_MINUTES[selected]
    if minutes is None:
        return history_limit
    return min(history_limit, max(requested * minutes + minutes, requested))


class TimeframeStreamAggregator:
    """Incrementally transform repeated forming 1m updates for one WebSocket."""

    def __init__(self, interval: str, seed: Iterable[KBar] = ()):
        self.interval = validate_timeframe(interval)
        self._key: tuple[object, ...] | None = None
        self._source: dict[tuple[str, datetime], KBar] = {}
        seed_bars = sorted(seed, key=lambda bar: bar.time)
        if seed_bars:
            self._key = timeframe_bucket(seed_bars[-1], self.interval)
            self._source = {
                (bar.contract, bar.time): bar
                for bar in seed_bars
                if timeframe_bucket(bar, self.interval) == self._key
            }

    def push(self, bar: KBar) -> list[KBar]:
        if self.interval == "1m":
            return [bar]
        key = timeframe_bucket(bar, self.interval)
        emitted: list[KBar] = []
        if self._key is not None and key != self._key and self._source:
            previous = _aggregate_group(list(self._source.values()), self.interval)
            emitted.append(previous.copy(status="closed"))
            self._source.clear()
        self._key = key
        self._source[(bar.contract, bar.time)] = bar
        current = _aggregate_group(list(self._source.values()), self.interval)
        emitted.append(current.copy(status="forming"))
        return emitted


def kbar_from_message(message: dict[str, object]) -> KBar:
    return KBar(
        symbol=str(message["symbol"]),
        contract=str(message["contract"]),
        time=datetime.fromisoformat(str(message["time"])),
        open=float(message["open"]),
        high=float(message["high"]),
        low=float(message["low"]),
        close=float(message["close"]),
        volume=int(message["volume"]),
        status=str(message["status"]),  # type: ignore[arg-type]
        session=str(message["session"]),  # type: ignore[arg-type]
        trading_date=date.fromisoformat(str(message["trading_date"])),
        first_tick_time=datetime.fromisoformat(str(message["time"])),
        last_tick_time=datetime.fromisoformat(str(message["exchange_time"])),
        exchange_time=datetime.fromisoformat(str(message["exchange_time"])),
        received_time=datetime.fromisoformat(str(message["received_time"])),
        latency_ms=float(message["latency_ms"]),
        no_trade=bool(message.get("no_trade", False)),
    )
