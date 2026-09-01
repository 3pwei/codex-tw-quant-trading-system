from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo


TAIPEI = ZoneInfo("Asia/Taipei")
Session = Literal["day", "night"]


@dataclass(frozen=True)
class TradingCalendar:
    holidays: frozenset[date] = frozenset()

    def next_trading_day(self, value: date) -> date:
        candidate = value + timedelta(days=1)
        while candidate.weekday() >= 5 or candidate in self.holidays:
            candidate += timedelta(days=1)
        return candidate


DEFAULT_CALENDAR = TradingCalendar()


def classify_tmf_session(
    exchange_time: datetime,
    calendar: TradingCalendar = DEFAULT_CALENDAR,
) -> tuple[Session, date]:
    """Return the TMF session and TAIFEX trading date.

    The fallback calendar skips weekends. Exchange holidays are intentionally
    delegated to the broker contract/calendar and can later replace this helper.
    """

    local = exchange_time.astimezone(TAIPEI)
    clock = local.time().replace(tzinfo=None)
    calendar_date = local.date()
    if time(8, 45) <= clock <= time(13, 45):
        return "day", calendar_date
    if clock >= time(15, 0):
        return "night", calendar.next_trading_day(calendar_date)
    if clock <= time(5, 0):
        return "night", calendar_date
    raise ValueError(f"{local.isoformat()} is outside the configured TMF sessions")


def minute_floor(value: datetime) -> datetime:
    return value.astimezone(TAIPEI).replace(second=0, microsecond=0)
