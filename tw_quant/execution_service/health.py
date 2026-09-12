from __future__ import annotations

from dataclasses import dataclass

from ..broker.identity import BrokerAccountRef
from .redaction import mask_account


@dataclass(frozen=True)
class BrokerConnectionHealth:
    connection_id: str
    broker_name: str
    account: BrokerAccountRef | None
    execution_state: str
    enabled: bool
    locked: bool
    recovery_status: str

    def to_public_dict(self) -> dict[str, object]:
        return {
            "broker_name": self.broker_name,
            "masked_account_id": mask_account(
                self.account.account_id if self.account is not None else None
            ),
            "execution_state": self.execution_state,
            "enabled": self.enabled,
            "locked": self.locked,
            "recovery_status": self.recovery_status,
        }


@dataclass(frozen=True)
class ExecutionServiceHealth:
    """Collection model ready for multiple isolated connections in a later PR."""

    connections: tuple[BrokerConnectionHealth, ...]

    def to_public_dict(self) -> dict[str, object]:
        return {
            "connections": [
                connection.to_public_dict() for connection in self.connections
            ]
        }
