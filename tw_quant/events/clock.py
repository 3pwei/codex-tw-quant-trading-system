from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _validate_time(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock time must be timezone-aware")


@dataclass
class VirtualClock:
    """Monotonic event time shared by backtest, replay, and paper trading."""

    current: datetime | None = None

    def __post_init__(self) -> None:
        if self.current is not None:
            _validate_time(self.current)

    def advance_to(self, value: datetime) -> datetime:
        _validate_time(value)
        if self.current is not None and value < self.current:
            raise ValueError("virtual clock cannot move backwards")
        self.current = value
        return value

