from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import heapq
from itertools import count
from typing import Callable, Iterable, TypeAlias

from .clock import VirtualClock
from .models import DomainEvent, EventKind


EventHandler: TypeAlias = Callable[[DomainEvent], Iterable[DomainEvent] | None]


@dataclass(frozen=True)
class ProcessedEvent:
    sequence: int
    event: DomainEvent


@dataclass(frozen=True)
class EngineRun:
    processed: tuple[ProcessedEvent, ...]
    duplicate_event_ids: tuple[str, ...]


class DeterministicEventEngine:
    """Synchronous, deterministic event loop used by replay and simulation.

    Events are ordered by event time and FIFO enqueue order. Handlers for a kind
    run in registration order. IDs are processed at most once, making restored
    queues and provider retries safe to replay.
    """

    def __init__(self, *, clock: VirtualClock | None = None, max_events: int = 1_000_000):
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self.clock = clock or VirtualClock()
        self.max_events = max_events
        self._handlers: dict[EventKind, list[EventHandler]] = defaultdict(list)
        self._queue: list[tuple[float, int, DomainEvent]] = []
        self._enqueue_sequence = count()
        self._processed_ids: set[str] = set()
        self._queued_ids: set[str] = set()
        self._duplicates: list[str] = []
        self._history: list[ProcessedEvent] = []

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def processed_ids(self) -> frozenset[str]:
        return frozenset(self._processed_ids)

    def subscribe(self, kind: EventKind, handler: EventHandler) -> None:
        self._handlers[kind].append(handler)

    def publish(self, event: DomainEvent) -> bool:
        event_id = event.meta.event_id
        if event_id in self._processed_ids or event_id in self._queued_ids:
            self._duplicates.append(event_id)
            return False
        if self.clock.current is not None and event.meta.occurred_at < self.clock.current:
            raise ValueError("cannot publish an event before the virtual clock")
        enqueue_sequence = next(self._enqueue_sequence)
        heapq.heappush(
            self._queue,
            (event.meta.occurred_at.timestamp(), enqueue_sequence, event),
        )
        self._queued_ids.add(event_id)
        return True

    def run(self) -> EngineRun:
        processed_this_run: list[ProcessedEvent] = []
        while self._queue:
            if len(processed_this_run) >= self.max_events:
                raise RuntimeError(f"event limit exceeded ({self.max_events})")
            _, _, event = heapq.heappop(self._queue)
            self._queued_ids.remove(event.meta.event_id)
            if event.meta.event_id in self._processed_ids:
                self._duplicates.append(event.meta.event_id)
                continue

            self.clock.advance_to(event.meta.occurred_at)
            record = ProcessedEvent(sequence=len(self._history) + 1, event=event)
            self._processed_ids.add(event.meta.event_id)
            self._history.append(record)
            processed_this_run.append(record)

            for handler in tuple(self._handlers[event.kind]):
                emitted = handler(event)
                if emitted is None:
                    continue
                for downstream in emitted:
                    if downstream.meta.occurred_at < event.meta.occurred_at:
                        raise ValueError(
                            "handler emitted an event before its cause: "
                            f"{downstream.meta.event_id}"
                        )
                    self.publish(downstream)

        return EngineRun(
            processed=tuple(processed_this_run),
            duplicate_event_ids=tuple(self._duplicates),
        )

    def history(self, kind: EventKind | None = None) -> tuple[ProcessedEvent, ...]:
        if kind is None:
            return tuple(self._history)
        return tuple(record for record in self._history if record.event.kind == kind)

    def events(self, kind: EventKind | None = None) -> tuple[DomainEvent, ...]:
        return tuple(record.event for record in self.history(kind))
