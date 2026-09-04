from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    RESEARCHER = "researcher"
    TRADER = "trader"
    ADMIN = "admin"


class AccountStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class TradingMode(str, Enum):
    DISABLED = "disabled"
    PAPER = "paper"
    LIVE = "live"


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    email: str
    role: Role
    status: AccountStatus
    trading_mode: TradingMode
    permissions: tuple[str, ...]
    access_subject: str | None = None
    registered: bool = True

    def to_message(self, authorization_enforced: bool) -> dict[str, object]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "role": self.role.value,
            "status": self.status.value,
            "trading_mode": self.trading_mode.value,
            "permissions": list(self.permissions),
            "registered": self.registered,
            "authorization_enforced": authorization_enforced,
        }
