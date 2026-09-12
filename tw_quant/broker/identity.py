from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class BrokerAccountRef:
    """Canonical identity for one account at one broker."""

    broker_name: str
    account_id: str

    def __post_init__(self) -> None:
        broker_name = self.broker_name.strip().lower()
        account_id = self.account_id.strip()
        if not broker_name or not account_id:
            raise ValueError("broker_name and account_id are required")
        object.__setattr__(self, "broker_name", broker_name)
        object.__setattr__(self, "account_id", account_id)
