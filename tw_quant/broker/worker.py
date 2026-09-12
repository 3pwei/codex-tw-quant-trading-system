from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Protocol, Sequence

from .audit import BrokerEventAuditStatus, SQLiteBrokerEventAuditRepository
from .callback_consumer import BrokerCallbackConsumer
from .events import BrokerEvent
from .manager import LiveOrderManager
from .ports import BrokerPort, CompositeOrderAdmissionGate, OrderAdmissionGate
from .reconciliation import (
    BrokerReconciliationSource,
    LiveReconciliationService,
    ReconciliationReport,
)
from .recovery import (
    RecoveryOrderGate,
    RecoveryStatus,
    SQLiteRecoveryLockRepository,
)
from .repository import SQLiteLiveOrderRepository


WorkerState = Literal["disabled", "starting", "running", "degraded", "stopped"]


@dataclass(frozen=True)
class ExecutionWorkerSettings:
    dispatch_poll_seconds: float = 0.1
    reconciliation_interval_seconds: float = 30.0
    heartbeat_seconds: float = 5.0
    callback_queue_size: int = 10_000

    def __post_init__(self) -> None:
        if min(
            self.dispatch_poll_seconds,
            self.reconciliation_interval_seconds,
            self.heartbeat_seconds,
        ) <= 0:
            raise ValueError("execution worker intervals must be positive")
        if not 1 <= self.callback_queue_size <= 100_000:
            raise ValueError("callback_queue_size must be between 1 and 100000")


class ExecutionWorkerMonitor(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def snapshot(self) -> dict[str, object]: ...


class DisabledExecutionWorker:
    """Production-safe monitor used until execution is explicitly composed."""

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    def snapshot(self) -> dict[str, object]:
        return {
            "state": "disabled",
            "recovery_status": "locked",
            "recovery_issues": ["execution_disabled"],
            "started_at": None,
            "heartbeat_at": None,
            "last_dispatch_at": None,
            "last_reconciliation_at": None,
            "callback_queue_depth": 0,
            "callback_queue_capacity": 0,
            "callback_events": 0,
            "callback_failures": 0,
            "unmatched_callbacks": 0,
            "dropped_callbacks": 0,
            "dispatches": 0,
            "reconciliations": 0,
            "failures": 0,
            "last_error": None,
        }


class ExecutionWorker:
    """Supervise durable dispatch, callback audit, and reconciliation loops."""

    def __init__(
        self,
        manager: LiveOrderManager,
        reconciliation: LiveReconciliationService,
        callback_consumer: BrokerCallbackConsumer,
        callback_queue: asyncio.Queue[BrokerEvent],
        *,
        settings: ExecutionWorkerSettings | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        if callback_queue.maxsize <= 0:
            raise ValueError("execution callback queue must be bounded")
        self.manager = manager
        self.reconciliation = reconciliation
        self.callback_consumer = callback_consumer
        self.callback_queue = callback_queue
        self.settings = settings or ExecutionWorkerSettings(
            callback_queue_size=callback_queue.maxsize
        )
        if self.settings.callback_queue_size != callback_queue.maxsize:
            raise ValueError("callback queue capacity does not match worker settings")
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.state: WorkerState = "stopped"
        self.started_at: datetime | None = None
        self.heartbeat_at: datetime | None = None
        self.last_dispatch_at: datetime | None = None
        self.last_reconciliation_at: datetime | None = None
        self.recovery_status = RecoveryStatus.LOCKED
        self.recovery_issues: tuple[str, ...] = ("reconciliation_required",)
        self.callback_events = 0
        self.callback_failures = 0
        self.unmatched_callbacks = 0
        self.dropped_callbacks = 0
        self.dispatches = 0
        self.reconciliations = 0
        self.failures = 0
        self.last_error: str | None = None
        self._stop_event = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._tasks:
            return
        self.state = "starting"
        self.started_at = self.now()
        self.heartbeat_at = self.started_at
        self._stop_event.clear()
        await self._reconcile()
        self._tasks = [
            asyncio.create_task(self._run_callbacks(), name="broker-callbacks"),
            asyncio.create_task(self._run_dispatch(), name="order-dispatch"),
            asyncio.create_task(
                self._run_reconciliation(), name="broker-reconciliation"
            ),
            asyncio.create_task(self._run_heartbeat(), name="execution-heartbeat"),
        ]

    async def stop(self) -> None:
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self.state = "stopped"
        self.heartbeat_at = self.now()

    def enqueue_callback(self, event: BrokerEvent) -> bool:
        try:
            self.callback_queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped_callbacks += 1
            self._record_failure(RuntimeError("broker callback queue is full"))
            return False
        return True

    async def _wait(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return False
        return True

    async def _reconcile(self) -> None:
        self.recovery_status = RecoveryStatus.RECONCILING
        self.recovery_issues = ("reconciliation_in_progress",)
        try:
            report = await self.reconciliation.reconcile()
            self._record_reconciliation(report)
        except Exception as exc:
            self.recovery_status = RecoveryStatus.LOCKED
            self.recovery_issues = ("reconciliation_worker_failed",)
            self._record_failure(exc)

    def _record_reconciliation(self, report: ReconciliationReport) -> None:
        self.reconciliations += 1
        self.last_reconciliation_at = self.now()
        self.recovery_status = report.state.status
        self.recovery_issues = report.state.issue_codes
        self.state = "running" if report.state.ready else "degraded"

    def _record_failure(self, exc: Exception) -> None:
        self.failures += 1
        self.last_error = f"{type(exc).__name__}: {exc}"[:1_000]
        self.recovery_status = RecoveryStatus.LOCKED
        self.recovery_issues = ("execution_worker_failed",)
        self.state = "degraded"

    def _lock_for_unmatched_callback(self) -> None:
        self.recovery_status = RecoveryStatus.LOCKED
        self.recovery_issues = ("unmatched_broker_callback",)
        self.state = "degraded"

    async def _run_callbacks(self) -> None:
        while not self._stop_event.is_set():
            try:
                event = await asyncio.wait_for(
                    self.callback_queue.get(), timeout=0.5
                )
            except asyncio.TimeoutError:
                continue
            try:
                record = await self.callback_consumer.consume_one(event)
                self.callback_events += 1
                if record.status is BrokerEventAuditStatus.FAILED:
                    self.callback_failures += 1
                    self._record_failure(
                        RuntimeError(record.error or "broker callback failed")
                    )
                elif record.status is BrokerEventAuditStatus.UNMATCHED:
                    self.unmatched_callbacks += 1
                    self._lock_for_unmatched_callback()
            except Exception as exc:
                self.callback_failures += 1
                self._record_failure(exc)
            finally:
                self.callback_queue.task_done()

    async def _run_dispatch(self) -> None:
        while not self._stop_event.is_set():
            if self.recovery_status is RecoveryStatus.READY:
                try:
                    result = await self.manager.dispatch_once()
                    if result is not None:
                        self.dispatches += 1
                        self.last_dispatch_at = self.now()
                        continue
                except Exception as exc:
                    self._record_failure(exc)
            if await self._wait(self.settings.dispatch_poll_seconds):
                return

    async def _run_reconciliation(self) -> None:
        while not await self._wait(
            self.settings.reconciliation_interval_seconds
        ):
            await self._reconcile()

    async def _run_heartbeat(self) -> None:
        while not await self._wait(self.settings.heartbeat_seconds):
            self.heartbeat_at = self.now()

    @staticmethod
    def _time(value: datetime | None) -> str | None:
        return value.isoformat(timespec="milliseconds") if value else None

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "recovery_status": self.recovery_status.value,
            "recovery_issues": list(self.recovery_issues),
            "started_at": self._time(self.started_at),
            "heartbeat_at": self._time(self.heartbeat_at),
            "last_dispatch_at": self._time(self.last_dispatch_at),
            "last_reconciliation_at": self._time(
                self.last_reconciliation_at
            ),
            "callback_queue_depth": self.callback_queue.qsize(),
            "callback_queue_capacity": self.callback_queue.maxsize,
            "callback_events": self.callback_events,
            "callback_failures": self.callback_failures,
            "unmatched_callbacks": self.unmatched_callbacks,
            "dropped_callbacks": self.dropped_callbacks,
            "dispatches": self.dispatches,
            "reconciliations": self.reconciliations,
            "failures": self.failures,
            "last_error": self.last_error,
        }


@dataclass
class ExecutionRuntime:
    worker: ExecutionWorker
    manager: LiveOrderManager
    callback_queue: asyncio.Queue[BrokerEvent]
    order_repository: SQLiteLiveOrderRepository
    audit_repository: SQLiteBrokerEventAuditRepository
    recovery_repository: SQLiteRecoveryLockRepository

    async def close(self) -> None:
        await self.worker.stop()
        self.audit_repository.close()
        self.recovery_repository.close()
        self.order_repository.close()


def build_execution_runtime(
    path: str | Path,
    *,
    broker_name: str,
    account_id: str,
    broker: BrokerPort,
    reconciliation_source: BrokerReconciliationSource,
    settings: ExecutionWorkerSettings | None = None,
    admission_gates: Sequence[OrderAdmissionGate] = (),
) -> ExecutionRuntime:
    """Compose the persistent worker without constructing credentials or SDKs."""

    broker_name = broker_name.strip()
    account_id = account_id.strip()
    if broker_name == "disabled":
        raise ValueError("disabled execution does not create a persistent runtime")
    if not broker_name or not account_id:
        raise ValueError("broker_name and account_id are required")
    if broker.broker_name != broker_name:
        raise ValueError("broker identity does not match execution runtime")
    worker_settings = settings or ExecutionWorkerSettings()
    order_repository = SQLiteLiveOrderRepository(path)
    audit_repository = SQLiteBrokerEventAuditRepository(path)
    recovery_repository = SQLiteRecoveryLockRepository(path)
    gate = CompositeOrderAdmissionGate((
        RecoveryOrderGate(recovery_repository, broker_name, account_id),
        *admission_gates,
    ))
    manager = LiveOrderManager(order_repository, broker, gate)
    reconciliation = LiveReconciliationService(
        broker_name=broker_name,
        account_id=account_id,
        order_store=order_repository,
        order_manager=manager,
        source=reconciliation_source,
        recovery_lock=recovery_repository,
    )
    consumer = BrokerCallbackConsumer(audit_repository, manager)
    callback_queue: asyncio.Queue[BrokerEvent] = asyncio.Queue(
        maxsize=worker_settings.callback_queue_size
    )
    worker = ExecutionWorker(
        manager,
        reconciliation,
        consumer,
        callback_queue,
        settings=worker_settings,
    )
    return ExecutionRuntime(
        worker=worker,
        manager=manager,
        callback_queue=callback_queue,
        order_repository=order_repository,
        audit_repository=audit_repository,
        recovery_repository=recovery_repository,
    )
