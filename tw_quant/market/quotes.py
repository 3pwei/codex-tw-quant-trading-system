from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from typing import Protocol


@dataclass(frozen=True)
class ExecutionQuote:
    """Canonical executable market snapshot; bid/ask are never synthesized."""

    symbol: str
    contract: str
    best_bid: float | None
    best_ask: float | None
    last_price: float | None
    exchange_time: datetime
    received_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.contract.strip() or not self.source.strip():
            raise ValueError("quote symbol, contract, and source are required")
        if self.exchange_time.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("quote timestamps must be timezone-aware")
        for price in (self.best_bid, self.best_ask, self.last_price):
            if price is not None and price <= 0:
                raise ValueError("quote prices must be positive")
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid > self.best_ask
        ):
            raise ValueError("best bid cannot exceed best ask")

    @property
    def complete(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None


class ExecutionQuoteView(Protocol):
    def get(self, symbol: str, contract: str) -> ExecutionQuote | None: ...


class ExecutionQuoteCache:
    """Thread-safe O(1) snapshots written by quote callbacks and read by decisions."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._quotes: dict[tuple[str, str], ExecutionQuote] = {}

    def update(self, quote: ExecutionQuote) -> None:
        key = (quote.symbol, quote.contract)
        with self._lock:
            current = self._quotes.get(key)
            if current is None or quote.received_at >= current.received_at:
                self._quotes[key] = quote

    def get(self, symbol: str, contract: str) -> ExecutionQuote | None:
        with self._lock:
            return self._quotes.get((symbol, contract))
