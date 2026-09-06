from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from ..events import DomainEvent, event_to_dict


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class SQLitePaperRepository:
    """Append-only paper event/control store isolated by platform owner."""

    def __init__(self, path: str | Path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(target, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    owner_user_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_paper_events_owner_sequence
                    ON paper_events(owner_user_id, sequence DESC);

                CREATE TABLE IF NOT EXISTS paper_idempotency (
                    owner_user_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(owner_user_id, idempotency_key)
                );

                CREATE TABLE IF NOT EXISTS paper_controls (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_paper_controls_owner_sequence
                    ON paper_controls(owner_user_id, sequence DESC);
                """
            )
            self.connection.commit()

    def order_for_key(self, owner_id: str, key: str) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT order_id FROM paper_idempotency "
                "WHERE owner_user_id=? AND idempotency_key=?",
                (owner_id, key),
            ).fetchone()
        return str(row["order_id"]) if row else None

    def reserve_key(self, owner_id: str, key: str, order_id: str) -> str:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO paper_idempotency VALUES (?, ?, ?, ?)",
                (owner_id, key, order_id, _now()),
            )
            row = self.connection.execute(
                "SELECT order_id FROM paper_idempotency "
                "WHERE owner_user_id=? AND idempotency_key=?",
                (owner_id, key),
            ).fetchone()
            self.connection.commit()
        assert row is not None
        return str(row["order_id"])

    def append_events(self, events: list[DomainEvent]) -> None:
        rows = []
        for event in events:
            owner_id = event.meta.owner_id
            if not owner_id:
                continue
            rows.append(
                (
                    event.meta.event_id,
                    owner_id,
                    event.kind,
                    event.meta.occurred_at.isoformat(timespec="microseconds"),
                    json.dumps(event_to_dict(event), sort_keys=True),
                    _now(),
                )
            )
        if not rows:
            return
        with self.lock:
            self.connection.executemany(
                "INSERT OR IGNORE INTO paper_events(" 
                "event_id, owner_user_id, kind, occurred_at, payload_json, recorded_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            self.connection.commit()

    def events(self, owner_id: str, limit: int = 500) -> list[dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT sequence, payload_json FROM paper_events "
                "WHERE owner_user_id=? ORDER BY sequence DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [
            {"sequence": int(row["sequence"]), **json.loads(row["payload_json"])}
            for row in rows
        ]

    def recovery_records(self) -> list[dict[str, object]]:
        """Return events and operator controls in durable write order."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT 'event' AS record_type, sequence, owner_user_id, "
                "payload_json, NULL AS action, NULL AS reason, occurred_at, "
                "recorded_at FROM paper_events "
                "UNION ALL "
                "SELECT 'control' AS record_type, sequence, owner_user_id, "
                "NULL AS payload_json, action, reason, occurred_at, recorded_at "
                "FROM paper_controls "
                "ORDER BY recorded_at, record_type, sequence"
            ).fetchall()
        records: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            payload = item.pop("payload_json")
            if payload is not None:
                try:
                    item["payload"] = json.loads(str(payload))
                except json.JSONDecodeError:
                    item["payload"] = None
                    item["parse_error"] = True
            records.append(item)
        return records

    def dangling_idempotency(self) -> list[dict[str, str]]:
        """Find reserved request keys whose order intent was never committed."""
        with self.lock:
            rows = self.connection.execute(
                "SELECT key_row.owner_user_id, key_row.idempotency_key, "
                "key_row.order_id FROM paper_idempotency AS key_row "
                "LEFT JOIN paper_events AS event_row "
                "ON event_row.owner_user_id=key_row.owner_user_id "
                "AND event_row.event_id=key_row.order_id "
                "AND event_row.kind='order_intent' "
                "WHERE event_row.event_id IS NULL"
            ).fetchall()
        return [
            {
                "owner_user_id": str(row["owner_user_id"]),
                "idempotency_key": str(row["idempotency_key"]),
                "order_id": str(row["order_id"]),
            }
            for row in rows
        ]

    def stats(self) -> dict[str, int]:
        with self.lock:
            row = self.connection.execute(
                "SELECT COUNT(*) AS events, "
                "COUNT(DISTINCT owner_user_id) AS owners FROM paper_events"
            ).fetchone()
            controls = self.connection.execute(
                "SELECT COUNT(*) AS total FROM paper_controls"
            ).fetchone()
        assert row is not None and controls is not None
        return {
            "events": int(row["events"]),
            "owners": int(row["owners"]),
            "controls": int(controls["total"]),
        }

    def order_snapshot(
        self, owner_id: str, order_id: str
    ) -> dict[str, object] | None:
        """Rebuild an API order view from its immutable event chain."""
        events = self.events(owner_id, 10_000)
        intent = next(
            (
                event for event in events
                if event["kind"] == "order_intent"
                and event.get("order_id") == order_id
            ),
            None,
        )
        if intent is None:
            return None
        decision = next(
            (
                event for event in events
                if event["kind"] == "risk_decision"
                and event.get("order_id") == order_id
            ),
            None,
        )
        fill = next(
            (
                event for event in events
                if event["kind"] == "fill"
                and event.get("order_id") == order_id
            ),
            None,
        )
        if fill:
            status = "filled"
            status_reason = "simulated_fill"
        elif decision and decision.get("approved"):
            status = "approved"
            status_reason = str(decision["reason"])
        elif decision:
            status = "rejected"
            status_reason = str(decision["reason"])
        else:
            status = "pending_risk"
            status_reason = "awaiting_risk"
        meta = intent["meta"]
        assert isinstance(meta, dict)
        return {
            "order_id": order_id,
            "submitted_at": meta["occurred_at"],
            "strategy_id": intent["strategy_id"],
            "strategy_version": intent["strategy_version"],
            "symbol": intent["symbol"],
            "contract": intent["contract"],
            "side": intent["side"],
            "quantity": intent["quantity"],
            "reduce_only": intent["reduce_only"],
            "reference_price": intent["reference_price"],
            "stop_loss_price": intent["stop_loss_price"],
            "status": status,
            "status_reason": status_reason,
            "approved_quantity": (
                decision.get("approved_quantity", 0) if decision else 0
            ),
            "fill_id": fill.get("fill_id") if fill else None,
        }

    def append_control(
        self, owner_id: str, action: str, reason: str, occurred_at: datetime
    ) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT INTO paper_controls(" 
                "owner_user_id, action, reason, occurred_at, recorded_at"
                ") VALUES (?, ?, ?, ?, ?)",
                (
                    owner_id,
                    action,
                    reason,
                    occurred_at.isoformat(timespec="microseconds"),
                    _now(),
                ),
            )
            self.connection.commit()

    def controls(self, owner_id: str, limit: int = 100) -> list[dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT sequence, action, reason, occurred_at FROM paper_controls "
                "WHERE owner_user_id=? ORDER BY sequence DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def close(self) -> None:
        self.connection.close()
