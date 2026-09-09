from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from .models import (
    BrokerOrder,
    BrokerOrderRequest,
    BrokerOrderStatus,
    ExecutionMode,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _request_json(request: BrokerOrderRequest) -> str:
    payload = asdict(request)
    payload["mode"] = request.mode.value
    return json.dumps(payload, sort_keys=True)


def _request(raw: str) -> BrokerOrderRequest:
    payload = json.loads(raw)
    payload["mode"] = ExecutionMode(payload["mode"])
    return BrokerOrderRequest(**payload)


class SQLiteLiveOrderRepository:
    """Durable order and outbox store. Unknown dispatches are never requeued."""

    def __init__(self, path: str | Path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(target, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS live_orders (
                    client_order_id TEXT PRIMARY KEY,
                    owner_user_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    broker_order_id TEXT,
                    filled_quantity INTEGER NOT NULL DEFAULT 0,
                    average_fill_price REAL,
                    status_reason TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_live_orders_owner_updated
                    ON live_orders(owner_user_id, updated_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_live_orders_broker_order
                    ON live_orders(broker_order_id)
                    WHERE broker_order_id IS NOT NULL;

                CREATE TABLE IF NOT EXISTS live_order_outbox (
                    client_order_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(client_order_id)
                        REFERENCES live_orders(client_order_id)
                );
                """
            )
            self.connection.commit()

    @staticmethod
    def _order(row: sqlite3.Row) -> BrokerOrder:
        return BrokerOrder(
            request=_request(str(row["request_json"])),
            status=BrokerOrderStatus(str(row["status"])),
            updated_at=datetime.fromisoformat(str(row["updated_at"])),
            broker_order_id=(
                str(row["broker_order_id"]) if row["broker_order_id"] else None
            ),
            filled_quantity=int(row["filled_quantity"]),
            average_fill_price=(
                float(row["average_fill_price"])
                if row["average_fill_price"] is not None else None
            ),
            status_reason=(
                str(row["status_reason"]) if row["status_reason"] else None
            ),
        )

    def reserve(
        self, request: BrokerOrderRequest, *, occurred_at: datetime | None = None
    ) -> tuple[BrokerOrder, bool]:
        if request.mode is not ExecutionMode.LIVE:
            raise ValueError("live order repository only accepts live orders")
        now = occurred_at or _utc_now()
        order = BrokerOrder(
            request=request,
            status=BrokerOrderStatus.RISK_APPROVED,
            updated_at=now,
            status_reason="durably_reserved",
        )
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO live_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    request.client_order_id,
                    request.owner_id,
                    _request_json(request),
                    order.status.value,
                    None,
                    0,
                    None,
                    order.status_reason,
                    now.isoformat(timespec="microseconds"),
                ),
            )
            created = cursor.rowcount == 1
            if created:
                self.connection.execute(
                    "INSERT INTO live_order_outbox VALUES (?, 'pending', 0, ?, ?)",
                    (
                        request.client_order_id,
                        now.isoformat(timespec="microseconds"),
                        now.isoformat(timespec="microseconds"),
                    ),
                )
            row = self.connection.execute(
                "SELECT * FROM live_orders WHERE client_order_id=?",
                (request.client_order_id,),
            ).fetchone()
            self.connection.commit()
        assert row is not None
        existing = self._order(row)
        if existing.request.owner_id != request.owner_id:
            raise ValueError("client_order_id already belongs to another owner")
        return existing, created

    def get(self, owner_id: str, client_order_id: str) -> BrokerOrder | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM live_orders WHERE owner_user_id=? AND client_order_id=?",
                (owner_id, client_order_id),
            ).fetchone()
        return self._order(row) if row else None

    def get_by_broker_order_id(self, broker_order_id: str) -> BrokerOrder | None:
        if not broker_order_id.strip():
            raise ValueError("broker_order_id is required")
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM live_orders WHERE broker_order_id=?",
                (broker_order_id,),
            ).fetchone()
        return self._order(row) if row else None

    def nonterminal_orders(self, owner_id: str | None = None) -> list[BrokerOrder]:
        terminal = tuple(status.value for status in BrokerOrderStatus if status.terminal)
        placeholders = ", ".join("?" for _ in terminal)
        query = f"SELECT * FROM live_orders WHERE status NOT IN ({placeholders})"
        parameters: tuple[object, ...] = terminal
        if owner_id is not None:
            query += " AND owner_user_id=?"
            parameters += (owner_id,)
        query += " ORDER BY updated_at, client_order_id"
        with self.lock:
            rows = self.connection.execute(query, parameters).fetchall()
        return [self._order(row) for row in rows]

    def claim_next(self) -> BrokerOrder | None:
        now = _utc_now().isoformat(timespec="microseconds")
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT orders.* FROM live_order_outbox AS outbox "
                "JOIN live_orders AS orders USING(client_order_id) "
                "WHERE outbox.state='pending' ORDER BY outbox.created_at LIMIT 1"
            ).fetchone()
            if row is None:
                self.connection.commit()
                return None
            self.connection.execute(
                "UPDATE live_order_outbox SET state='processing', "
                "attempt_count=attempt_count+1, updated_at=? WHERE client_order_id=?",
                (now, row["client_order_id"]),
            )
            self.connection.commit()
        return self._order(row)

    def finish_dispatch(self, order: BrokerOrder) -> None:
        outbox_state = (
            "blocked" if order.status is BrokerOrderStatus.UNKNOWN else "completed"
        )
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            self._update_order(order)
            self.connection.execute(
                "UPDATE live_order_outbox SET state=?, updated_at=? "
                "WHERE client_order_id=?",
                (
                    outbox_state,
                    order.updated_at.isoformat(timespec="microseconds"),
                    order.request.client_order_id,
                ),
            )
            self.connection.commit()

    def save_reconciliation(self, order: BrokerOrder) -> None:
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            self._update_order(order)
            if order.status is not BrokerOrderStatus.UNKNOWN:
                self.connection.execute(
                    "UPDATE live_order_outbox SET state='resolved', updated_at=? "
                    "WHERE client_order_id=? AND state='blocked'",
                    (
                        order.updated_at.isoformat(timespec="microseconds"),
                        order.request.client_order_id,
                    ),
                )
            self.connection.commit()

    def _update_order(self, order: BrokerOrder) -> None:
        self.connection.execute(
            "UPDATE live_orders SET status=?, broker_order_id=?, "
            "filled_quantity=?, average_fill_price=?, status_reason=?, updated_at=? "
            "WHERE client_order_id=? AND owner_user_id=?",
            (
                order.status.value,
                order.broker_order_id,
                order.filled_quantity,
                order.average_fill_price,
                order.status_reason,
                order.updated_at.isoformat(timespec="microseconds"),
                order.request.client_order_id,
                order.request.owner_id,
            ),
        )

    def recover_interrupted_dispatches(self) -> int:
        """Block crash-interrupted work; a broker lookup must resolve it."""
        now = _utc_now()
        with self.lock:
            rows = self.connection.execute(
                "SELECT orders.* FROM live_order_outbox AS outbox "
                "JOIN live_orders AS orders USING(client_order_id) "
                "WHERE outbox.state='processing'"
            ).fetchall()
            for row in rows:
                order = self._order(row)
                unknown = BrokerOrder(
                    request=order.request,
                    status=BrokerOrderStatus.UNKNOWN,
                    updated_at=now,
                    broker_order_id=order.broker_order_id,
                    filled_quantity=order.filled_quantity,
                    average_fill_price=order.average_fill_price,
                    status_reason="dispatch_interrupted_reconciliation_required",
                )
                self._update_order(unknown)
                self.connection.execute(
                    "UPDATE live_order_outbox SET state='blocked', updated_at=? "
                    "WHERE client_order_id=?",
                    (now.isoformat(timespec="microseconds"), order.request.client_order_id),
                )
            self.connection.commit()
        return len(rows)

    def outbox_state(self, client_order_id: str) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT state FROM live_order_outbox WHERE client_order_id=?",
                (client_order_id,),
            ).fetchone()
        return str(row["state"]) if row else None

    def close(self) -> None:
        self.connection.close()
