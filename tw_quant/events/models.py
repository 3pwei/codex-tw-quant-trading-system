from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from hashlib import sha256
import json
from typing import Literal, TypeAlias


EventKind = Literal[
    "market",
    "bar_closed",
    "signal",
    "order_intent",
    "risk_decision",
    "fill",
    "position",
    "session",
]
Direction = Literal["long", "short"]
OrderSide = Literal["buy", "sell"]


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def deterministic_event_id(
    kind: EventKind,
    occurred_at: datetime,
    source: str,
    source_key: str,
) -> str:
    """Return a stable event ID for the same source fact across restarts/replays."""
    _require_aware(occurred_at, "occurred_at")
    if not source or not source_key:
        raise ValueError("source and source_key are required")
    raw = "|".join((kind, occurred_at.isoformat(timespec="microseconds"), source, source_key))
    return sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EventMetadata:
    event_id: str
    occurred_at: datetime
    source: str
    correlation_id: str | None = None
    causation_id: str | None = None
    owner_id: str | None = None

    def __post_init__(self) -> None:
        if not self.event_id or not self.source:
            raise ValueError("event_id and source are required")
        _require_aware(self.occurred_at, "occurred_at")

    @classmethod
    def create(
        cls,
        *,
        kind: EventKind,
        occurred_at: datetime,
        source: str,
        source_key: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        owner_id: str | None = None,
    ) -> "EventMetadata":
        event_id = deterministic_event_id(kind, occurred_at, source, source_key)
        return cls(
            event_id=event_id,
            occurred_at=occurred_at,
            source=source,
            correlation_id=correlation_id or event_id,
            causation_id=causation_id,
            owner_id=owner_id,
        )


@dataclass(frozen=True)
class MarketEvent:
    meta: EventMetadata
    symbol: str
    contract: str
    price: float
    volume: int
    kind: Literal["market"] = field(default="market", init=False)

    def __post_init__(self) -> None:
        if not self.symbol or not self.contract:
            raise ValueError("symbol and contract are required")
        if self.price <= 0:
            raise ValueError("price must be positive")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")


@dataclass(frozen=True)
class BarClosedEvent:
    meta: EventMetadata
    symbol: str
    contract: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    session: Literal["day", "night"]
    trading_date: date
    kind: Literal["bar_closed"] = field(default="bar_closed", init=False)

    def __post_init__(self) -> None:
        if not self.symbol or not self.contract or not self.timeframe:
            raise ValueError("symbol, contract, and timeframe are required")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")


@dataclass(frozen=True)
class SignalEvent:
    meta: EventMetadata
    strategy_id: str
    strategy_version: int
    symbol: str
    direction: Direction
    action: Literal["enter", "exit"]
    reference_price: float
    reason: str
    kind: Literal["signal"] = field(default="signal", init=False)

    def __post_init__(self) -> None:
        if not self.strategy_id or self.strategy_version < 1:
            raise ValueError("strategy_id and positive strategy_version are required")
        if self.reference_price <= 0:
            raise ValueError("reference_price must be positive")


@dataclass(frozen=True)
class OrderIntent:
    meta: EventMetadata
    order_id: str
    strategy_id: str
    strategy_version: int
    symbol: str
    contract: str
    side: OrderSide
    quantity: int
    order_type: Literal["market"] = "market"
    kind: Literal["order_intent"] = field(default="order_intent", init=False)

    def __post_init__(self) -> None:
        if not self.order_id or not self.strategy_id:
            raise ValueError("order_id and strategy_id are required")
        if self.strategy_version < 1 or self.quantity <= 0:
            raise ValueError("strategy_version and quantity must be positive")


@dataclass(frozen=True)
class RiskDecision:
    meta: EventMetadata
    order_id: str
    approved: bool
    approved_quantity: int
    reason: str
    kind: Literal["risk_decision"] = field(default="risk_decision", init=False)

    def __post_init__(self) -> None:
        if not self.order_id or not self.reason:
            raise ValueError("order_id and reason are required")
        if self.approved_quantity < 0:
            raise ValueError("approved_quantity cannot be negative")
        if self.approved and self.approved_quantity == 0:
            raise ValueError("approved decisions require a positive quantity")
        if not self.approved and self.approved_quantity != 0:
            raise ValueError("rejected decisions must have zero quantity")


@dataclass(frozen=True)
class FillEvent:
    meta: EventMetadata
    fill_id: str
    order_id: str
    symbol: str
    contract: str
    side: OrderSide
    quantity: int
    price: float
    commission: float = 0.0
    tax: float = 0.0
    slippage: float = 0.0
    kind: Literal["fill"] = field(default="fill", init=False)

    def __post_init__(self) -> None:
        if not self.fill_id or not self.order_id:
            raise ValueError("fill_id and order_id are required")
        if self.quantity <= 0 or self.price <= 0:
            raise ValueError("quantity and price must be positive")
        if min(self.commission, self.tax, self.slippage) < 0:
            raise ValueError("fill costs cannot be negative")


@dataclass(frozen=True)
class PositionEvent:
    meta: EventMetadata
    symbol: str
    contract: str
    quantity: int
    average_price: float
    realized_pnl: float
    unrealized_pnl: float
    kind: Literal["position"] = field(default="position", init=False)

    def __post_init__(self) -> None:
        if not self.symbol or not self.contract:
            raise ValueError("symbol and contract are required")
        if self.quantity != 0 and self.average_price <= 0:
            raise ValueError("open positions require a positive average_price")
        if self.quantity == 0 and self.average_price != 0:
            raise ValueError("flat positions must have zero average_price")


@dataclass(frozen=True)
class SessionEvent:
    meta: EventMetadata
    symbol: str
    contract: str
    session: Literal["day", "night"]
    trading_date: date
    action: Literal["opened", "closing", "closed"]
    kind: Literal["session"] = field(default="session", init=False)

    def __post_init__(self) -> None:
        if not self.symbol or not self.contract:
            raise ValueError("symbol and contract are required")


DomainEvent: TypeAlias = (
    MarketEvent
    | BarClosedEvent
    | SignalEvent
    | OrderIntent
    | RiskDecision
    | FillEvent
    | PositionEvent
    | SessionEvent
)


def event_to_dict(event: DomainEvent) -> dict[str, object]:
    """Serialize an event to JSON-compatible primitives for audit storage/APIs."""
    payload = asdict(event)
    payload["kind"] = event.kind
    payload["meta"]["occurred_at"] = event.meta.occurred_at.isoformat(timespec="microseconds")
    if isinstance(event, (BarClosedEvent, SessionEvent)):
        payload["trading_date"] = event.trading_date.isoformat()
    # Fail early if a future event adds a value that is not audit-log serializable.
    json.dumps(payload, sort_keys=True)
    return payload
