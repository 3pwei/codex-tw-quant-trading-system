from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from .aggregator import MinuteBarAggregator
from .feed import MarketFeed
from .hub import BroadcastHub
from .models import ConnectionStatus, TickEvent, isoformat_millis
from .storage import BarRepository
from .sessions import DEFAULT_CALENDAR, TradingCalendar


TAIPEI = ZoneInfo("Asia/Taipei")


class LiveMarketService:
    def __init__(
        self,
        feed: MarketFeed,
        repository: BarRepository,
        symbol: str = "TMF",
        heartbeat_seconds: float = 5.0,
        calendar: TradingCalendar = DEFAULT_CALENDAR,
        history_limit: int = 500,
    ):
        self.feed = feed
        self.repository = repository
        self.symbol = symbol
        self.heartbeat_seconds = heartbeat_seconds
        self.history_limit = history_limit
        self.queue: asyncio.Queue[TickEvent] = asyncio.Queue(maxsize=20_000)
        self.hub = BroadcastHub()
        self.aggregator = MinuteBarAggregator(symbol, calendar=calendar)
        self.connection_status: ConnectionStatus = "connecting"
        self.last_tick_time: datetime | None = None
        self.last_received_time: datetime | None = None
        self.last_heartbeat_time: datetime | None = None
        self.dropped_ticks = 0
        self.history_bars_loaded = 0
        self.history_error: str | None = None
        self._worker: asyncio.Task | None = None
        self._heartbeat: asyncio.Task | None = None
        self._running = False

    def enqueue_tick(self, tick: TickEvent) -> None:
        try:
            self.queue.put_nowait(tick)
        except asyncio.QueueFull:
            self.dropped_ticks += 1

    def set_connection_status(self, status: ConnectionStatus) -> None:
        self.connection_status = status
        self.hub.publish(self.status_message())

    async def start(self) -> None:
        self._running = True
        self.aggregator.restore(self.repository.latest_forming(self.symbol))
        self._worker = asyncio.create_task(self._run_worker(), name="tmf-kbar-worker")
        self._heartbeat = asyncio.create_task(
            self._run_heartbeat(), name="tmf-feed-heartbeat"
        )
        await self.feed.start(self.enqueue_tick, self.set_connection_status)
        try:
            history = await self.feed.load_history(self.history_limit)
            if history:
                purge = getattr(self.repository, "purge_backfill", None)
                if purge is not None:
                    purge(self.symbol, history[-1].contract)
            for bar in history:
                self.repository.save(bar)
            self.history_bars_loaded = len(history)
        except Exception as exc:
            # Historical backfill must not interrupt the live Tick stream.
            self.history_error = str(exc)

    async def stop(self) -> None:
        self._running = False
        await self.feed.stop()
        for task in (self._worker, self._heartbeat):
            if task:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._worker, self._heartbeat) if task),
            return_exceptions=True,
        )
        current = self.aggregator.current
        if current:
            self.repository.save(current)

    async def _run_worker(self) -> None:
        while self._running:
            tick = await self.queue.get()
            self.last_tick_time = tick.exchange_time
            self.last_received_time = tick.received_time
            if self.repository.tick_seen(tick.dedup_key):
                self.aggregator.duplicate_ticks += 1
                self.queue.task_done()
                continue
            self.repository.remember_tick(tick.dedup_key, tick.exchange_time)
            result = self.aggregator.process(tick)
            for bar in result.bars:
                self.repository.save(bar)
                self.hub.publish(bar.to_message(self.connection_status))
            self.queue.task_done()

    async def _run_heartbeat(self) -> None:
        while self._running:
            healthy = False
            try:
                healthy = await self.feed.heartbeat()
            except Exception:
                healthy = False
            self.last_heartbeat_time = datetime.now(TAIPEI)
            if not healthy:
                self.connection_status = "disconnected"
            self.hub.publish(self.status_message(message_type="heartbeat"))
            await asyncio.sleep(self.heartbeat_seconds)

    def status_message(self, message_type: str = "status") -> dict[str, object]:
        now = datetime.now(TAIPEI)
        latency_ms = None
        tick_age_ms = None
        if self.last_tick_time and self.last_received_time:
            latency_ms = max(
                0.0,
                (self.last_received_time - self.last_tick_time).total_seconds() * 1000,
            )
            tick_age_ms = max(0.0, (now - self.last_received_time).total_seconds() * 1000)
        return {
            "type": message_type,
            "symbol": self.symbol,
            "contract": self.feed.contract,
            "connection_status": self.connection_status,
            "last_tick_time": isoformat_millis(self.last_tick_time)
            if self.last_tick_time else None,
            "last_heartbeat_time": isoformat_millis(self.last_heartbeat_time)
            if self.last_heartbeat_time else None,
            "server_time": isoformat_millis(now),
            "latency_ms": round(latency_ms, 3) if latency_ms is not None else None,
            "tick_age_ms": round(tick_age_ms, 3) if tick_age_ms is not None else None,
            "queue_size": self.queue.qsize(),
            "dropped_ticks": self.dropped_ticks,
            "duplicate_ticks": self.aggregator.duplicate_ticks,
            "late_ticks": self.aggregator.late_ticks,
            "history_bars_loaded": self.history_bars_loaded,
            "history_error": self.history_error,
        }
