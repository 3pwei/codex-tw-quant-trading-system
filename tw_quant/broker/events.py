from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Mapping


MAX_BROKER_EVENT_BYTES = 65_536


@dataclass(frozen=True)
class BrokerEvent:
    """Normalized broker callback accepted at the SDK boundary."""

    event_id: str
    broker_name: str
    event_type: str
    broker_order_id: str | None
    received_at: datetime
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.event_id or not self.broker_name or not self.event_type:
            raise ValueError("broker event identity is required")
        if self.received_at.tzinfo is None or self.received_at.utcoffset() is None:
            raise ValueError("broker event received_at must be timezone-aware")
        encoded = canonical_payload(self.payload).encode()
        if len(encoded) > MAX_BROKER_EVENT_BYTES:
            raise ValueError("broker event payload is too large")
        expected = broker_event_id(
            self.broker_name,
            self.event_type,
            self.broker_order_id,
            self.payload,
        )
        if self.event_id != expected:
            raise ValueError("broker event ID does not match its content")


def canonical_payload(payload: Mapping[str, object]) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def broker_event_id(
    broker_name: str,
    event_type: str,
    broker_order_id: str | None,
    payload: Mapping[str, object],
) -> str:
    """Return a stable ID so repeated callbacks are durably idempotent."""

    identity = canonical_payload({
        "broker": broker_name,
        "event_type": event_type,
        "broker_order_id": broker_order_id,
        "payload": payload,
    })
    return hashlib.sha256(identity.encode()).hexdigest()
