from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
from typing import Literal


BarStatus = Literal["forming", "closed"]
ConnectionStatus = Literal["connecting", "connected", "reconnecting", "disconnected"]


def isoformat_millis(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class TickEvent:
    symbol: str
    contract: str
    exchange_time: datetime
    received_time: datetime
    price: float
    volume: int
    total_volume: int | None = None
    sequence: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol or not self.contract:
            raise ValueError("symbol and contract are required")
        if self.exchange_time.tzinfo is None or self.received_time.tzinfo is None:
            raise ValueError("tick timestamps must be timezone-aware")
        if self.price <= 0:
            raise ValueError("tick price must be positive")
        if self.volume < 0:
            raise ValueError("tick volume cannot be negative")

    @property
    def dedup_key(self) -> str:
        if self.sequence:
            return f"{self.contract}:{self.sequence}"
        raw = "|".join(
            (
                self.contract,
                self.exchange_time.isoformat(timespec="microseconds"),
                f"{self.price:.10f}",
                str(self.volume),
                str(self.total_volume if self.total_volume is not None else ""),
            )
        )
        return sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class KBar:
    symbol: str
    contract: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    status: BarStatus
    session: Literal["day", "night"]
    trading_date: date
    first_tick_time: datetime
    last_tick_time: datetime
    exchange_time: datetime
    received_time: datetime
    latency_ms: float
    no_trade: bool = False

    def copy(self, **changes: object) -> "KBar":
        values = asdict(self)
        values.update(changes)
        return KBar(**values)

    def to_message(
        self, connection_status: ConnectionStatus, interval: str = "1m"
    ) -> dict[str, object]:
        return {
            "type": "kbar",
            "interval": interval,
            "symbol": self.symbol,
            "contract": self.contract,
            "exchange_time": isoformat_millis(self.exchange_time),
            "received_time": isoformat_millis(self.received_time),
            "latency_ms": round(self.latency_ms, 3),
            "time": isoformat_millis(self.time),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "status": self.status,
            "connection_status": connection_status,
            "session": self.session,
            "trading_date": self.trading_date.isoformat(),
            "no_trade": self.no_trade,
        }
