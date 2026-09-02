from __future__ import annotations

import asyncio
import csv
from datetime import datetime, time, timedelta
from pathlib import Path
from time import monotonic
from typing import Callable, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from .models import ConnectionStatus, KBar, TickEvent
from .sessions import classify_tmf_session, minute_floor


TAIPEI = ZoneInfo("Asia/Taipei")
TickCallback = Callable[[TickEvent], None]
StatusCallback = Callable[[ConnectionStatus], None]


class MarketFeed(Protocol):
    contract: str
    async def start(self, on_tick: TickCallback, on_status: StatusCallback) -> None: ...
    async def stop(self) -> None: ...
    async def heartbeat(self) -> bool: ...
    async def load_history(self, limit: int) -> list[KBar]: ...


def parse_exchange_time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=TAIPEI)
    return result.astimezone(TAIPEI)


class ReplayFeed:
    """Replay deterministic tick CSV data without broker credentials."""

    def __init__(self, csv_path: str | Path, speed: float = 8.0, loop: bool = True):
        self.csv_path = Path(csv_path)
        self.speed = speed
        self.loop = loop
        self.contract = ""
        self._task: asyncio.Task | None = None
        self._healthy = False

    def load(self) -> list[dict[str, str]]:
        with self.csv_path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        required = {"exchange_time", "symbol", "contract", "price", "volume"}
        if not rows or not required.issubset(rows[0]):
            raise ValueError(f"replay CSV requires columns: {', '.join(sorted(required))}")
        self.contract = rows[0]["contract"]
        return rows

    @staticmethod
    def _live_anchor(now: datetime) -> datetime:
        """Return a current, session-valid exchange clock for looping mock data."""
        local = now.astimezone(TAIPEI)
        try:
            classify_tmf_session(local)
            return local
        except ValueError:
            clock = local.time().replace(tzinfo=None)
            opening = time(8, 45) if time(5, 0) < clock < time(8, 45) else time(15, 0)
            return local.replace(
                hour=opening.hour, minute=opening.minute, second=0, microsecond=0
            )

    async def start(self, on_tick: TickCallback, on_status: StatusCallback) -> None:
        rows = self.load()
        self._healthy = True
        on_status("connected")

        async def replay() -> None:
            sequence = 0
            replay_id = uuid4().hex
            live_started_at = monotonic()
            live_exchange_anchor = self._live_anchor(datetime.now(TAIPEI))
            while self._healthy:
                previous: datetime | None = None
                for row in rows:
                    if not self._healthy:
                        return
                    source_exchange_time = parse_exchange_time(row["exchange_time"])
                    if previous is not None:
                        source_delay = max(
                            0.02,
                            min(
                                (source_exchange_time - previous).total_seconds()
                                / self.speed,
                                0.5,
                            ),
                        )
                        await asyncio.sleep(source_delay)
                    previous = source_exchange_time
                    sequence += 1
                    source_latency = float(row.get("latency_ms") or 12.0)
                    if self.loop:
                        # A looping replay behaves like a live feed: its exchange clock
                        # advances with wall time and every emitted event is unique.
                        # The original CSV sequence must not trigger persistent dedup
                        # after the first loop or after a process restart.
                        exchange_time = live_exchange_anchor + timedelta(
                            seconds=monotonic() - live_started_at
                        )
                        event_sequence = f"replay-{replay_id}-{sequence}"
                    else:
                        # One-shot replay remains deterministic for tests/backtests.
                        exchange_time = source_exchange_time
                        event_sequence = row.get("sequence") or f"replay-{sequence}"
                    received_time = exchange_time + timedelta(milliseconds=source_latency)
                    on_tick(
                        TickEvent(
                            symbol=row["symbol"], contract=row["contract"],
                            exchange_time=exchange_time, received_time=received_time,
                            price=float(row["price"]), volume=int(float(row["volume"])),
                            total_volume=int(float(row["total_volume"]))
                            if row.get("total_volume") else None,
                            sequence=event_sequence,
                        )
                    )
                if not self.loop:
                    return
                await asyncio.sleep(0.5)

        self._task = asyncio.create_task(replay(), name="tmf-replay-feed")

    async def stop(self) -> None:
        self._healthy = False
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def heartbeat(self) -> bool:
        return self._healthy and self._task is not None and not self._task.done()

    async def load_history(self, limit: int) -> list[KBar]:
        return []


class ShioajiFeed:
    """Quote-only Shioaji TMF feed. This class never activates CA or places orders."""

    def __init__(
        self, api_key: str, secret_key: str, contract: str, production: bool,
        history_days: int = 7,
    ):
        self.api_key = api_key
        self.secret_key = secret_key
        self.requested_contract = contract
        self.contract = contract
        self.production = production
        self.history_days = history_days
        self.api = None
        self._resolved_contract = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._on_tick: TickCallback | None = None
        self._on_status: StatusCallback | None = None
        self._connected = False

    async def start(self, on_tick: TickCallback, on_status: StatusCallback) -> None:
        self._loop = asyncio.get_running_loop()
        self._on_tick = on_tick
        self._on_status = on_status
        on_status("connecting")
        await asyncio.to_thread(self._login_and_subscribe)

    def _emit_status(self, status: ConnectionStatus) -> None:
        self._connected = status == "connected"
        if self._loop and self._on_status:
            self._loop.call_soon_threadsafe(self._on_status, status)

    def _login_and_subscribe(self) -> None:
        try:
            import shioaji as sj
        except ImportError as exc:
            raise RuntimeError("install the shioaji optional dependency") from exc

        self.api = sj.Shioaji(simulation=not self.production)
        self.api.login(
            api_key=self.api_key,
            secret_key=self.secret_key,
            subscribe_trade=False,
        )
        contracts = self.api.Contracts.Futures.TMF
        contract = getattr(contracts, self.requested_contract, None)
        if contract is None:
            contract = getattr(contracts, "TMFR1", None)
        if contract is None:
            raise RuntimeError("TMF front-month contract was not found")
        self._resolved_contract = contract
        self.contract = getattr(contract, "target_code", None) or contract.code

        def tick_callback(_exchange, tick) -> None:
            # Broker callback boundary: validate/normalize and enqueue only.
            if self._loop is None or self._on_tick is None:
                return
            exchange_time = tick.datetime
            if exchange_time.tzinfo is None:
                exchange_time = exchange_time.replace(tzinfo=TAIPEI)
            received_time = datetime.now(TAIPEI)
            sequence = None
            for name in ("sequence", "seqno", "tick_sn"):
                value = getattr(tick, name, None)
                if value is not None:
                    sequence = str(value)
                    break
            try:
                event = TickEvent(
                    symbol="TMF",
                    contract=tick.code or self.contract,
                    exchange_time=exchange_time.astimezone(TAIPEI),
                    received_time=received_time,
                    price=float(tick.close),
                    volume=int(tick.volume),
                    total_volume=int(tick.total_volume),
                    sequence=sequence,
                )
            except (TypeError, ValueError):
                return
            self._loop.call_soon_threadsafe(self._on_tick, event)

        def event_callback(_resp_code: int, event_code: int, _info: str, _event: str):
            if event_code in {0, 13, 16}:
                self._emit_status("connected")
            elif event_code == 12:
                self._emit_status("reconnecting")
            elif event_code in {1, 2}:
                self._emit_status("disconnected")

        self.api.quote.set_on_tick_fop_v1_callback(tick_callback)
        self.api.quote.set_event_callback(event_callback)
        self.api.quote.subscribe(
            contract,
            quote_type=sj.constant.QuoteType.Tick,
            version=sj.constant.QuoteVersion.v1,
        )
        self._emit_status("connected")

    @staticmethod
    def _historical_time(value) -> datetime:
        if isinstance(value, datetime):
            result = value
            if result.tzinfo is None:
                result = result.replace(tzinfo=TAIPEI)
            return result.astimezone(TAIPEI)
        if isinstance(value, str):
            return parse_exchange_time(value)
        raw = float(value)
        if raw > 1e16:
            raw /= 1e9
        elif raw > 1e13:
            raw /= 1e6
        elif raw > 1e10:
            raw /= 1e3
        return datetime.fromtimestamp(raw, TAIPEI)

    def _load_history_sync(self, limit: int) -> list[KBar]:
        if self.api is None or self._resolved_contract is None:
            return []
        now = datetime.now(TAIPEI)
        end = now.date()
        start = end - timedelta(days=self.history_days - 1)
        payload = self.api.kbars(
            contract=self._resolved_contract,
            start=start.isoformat(),
            end=end.isoformat(),
        ).dict()
        rows = zip(
            payload.get("ts", ()), payload.get("Open", ()),
            payload.get("High", ()), payload.get("Low", ()),
            payload.get("Close", ()), payload.get("Volume", ()),
        )
        current_minute = minute_floor(now)
        bars: list[KBar] = []
        for timestamp, open_, high, low, close, volume in rows:
            # Shioaji labels a one-minute K bar by its right edge (09:01 is
            # the 09:00 minute). The live aggregator stores the left edge.
            bar_time = minute_floor(
                self._historical_time(timestamp) - timedelta(minutes=1)
            )
            if bar_time >= current_minute:
                continue
            open_, high, low, close = map(float, (open_, high, low, close))
            volume = int(volume)
            if (
                min(open_, high, low, close) <= 0
                or high < max(open_, close)
                or low > min(open_, close)
                or volume < 0
            ):
                continue
            try:
                session, trading_date = classify_tmf_session(bar_time)
            except ValueError:
                continue
            last_tick_time = bar_time + timedelta(seconds=59, milliseconds=999)
            bars.append(KBar(
                symbol="TMF", contract=self.contract, time=bar_time,
                open=open_, high=high, low=low, close=close, volume=volume,
                status="closed", session=session, trading_date=trading_date,
                first_tick_time=bar_time, last_tick_time=last_tick_time,
                exchange_time=last_tick_time, received_time=now,
                latency_ms=0.0,
            ))
        return bars[-limit:]

    async def load_history(self, limit: int) -> list[KBar]:
        return await asyncio.to_thread(self._load_history_sync, limit)

    async def stop(self) -> None:
        if self.api is None:
            return
        try:
            await asyncio.to_thread(self.api.logout)
        finally:
            self._emit_status("disconnected")

    async def heartbeat(self) -> bool:
        # Shioaji's Solace client owns the wire heartbeat/reconnect loop. The
        # quote event callback reports that independent connection state.
        return self._connected and self.api is not None
