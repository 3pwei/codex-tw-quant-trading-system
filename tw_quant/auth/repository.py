from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .access import AccessIdentity
from .models import AccountStatus, AuthUser, Role, TradingMode
from .permissions import PERMISSIONS, ROLE_PERMISSIONS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_email(email: str) -> str:
    return email.strip().casefold()


class SQLiteAuthRepository:
    """Application identities stored beside market data in SQLite.

    The interface is intentionally isolated from ``SQLiteBarRepository`` so a
    future PostgreSQL identity store does not affect market-data persistence.
    """

    def __init__(self, path: str | Path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(target, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self.lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS auth_schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_users (
                    user_id TEXT PRIMARY KEY,
                    access_subject TEXT UNIQUE,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    role TEXT NOT NULL CHECK(role IN ('researcher','trader','admin')),
                    status TEXT NOT NULL CHECK(status IN ('active','suspended','revoked')),
                    trading_mode TEXT NOT NULL
                        CHECK(trading_mode IN ('disabled','paper','live')),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT
                );

                CREATE TABLE IF NOT EXISTS permissions (
                    permission_key TEXT PRIMARY KEY,
                    description TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS role_permissions (
                    role TEXT NOT NULL,
                    permission_key TEXT NOT NULL,
                    PRIMARY KEY(role, permission_key),
                    FOREIGN KEY(permission_key) REFERENCES permissions(permission_key)
                        ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    actor_user_id TEXT,
                    subject_user_id TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    details_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(actor_user_id) REFERENCES app_users(user_id)
                        ON DELETE SET NULL,
                    FOREIGN KEY(subject_user_id) REFERENCES app_users(user_id)
                        ON DELETE SET NULL
                );

                CREATE INDEX IF NOT EXISTS idx_audit_events_created
                    ON audit_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_audit_events_actor
                    ON audit_events(actor_user_id, created_at DESC);
                """
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO auth_schema_migrations VALUES (1, ?)",
                (_now(),),
            )
            self.connection.executemany(
                "INSERT OR IGNORE INTO permissions VALUES (?, ?)",
                PERMISSIONS.items(),
            )
            self.connection.executemany(
                "INSERT OR IGNORE INTO role_permissions VALUES (?, ?)",
                [
                    (role.value, permission)
                    for role, permissions in ROLE_PERMISSIONS.items()
                    for permission in permissions
                ],
            )
            self.connection.commit()

    def bootstrap_admins(self, emails: tuple[str, ...]) -> None:
        now = _now()
        with self.lock:
            for raw_email in emails:
                email = normalize_email(raw_email)
                if not email:
                    continue
                existing = self.connection.execute(
                    "SELECT role FROM app_users WHERE email=?", (email,)
                ).fetchone()
                if existing and existing["role"] != Role.ADMIN.value:
                    raise ValueError(
                        "bootstrap admin email is registered as a non-admin"
                    )
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO app_users(
                        user_id, access_subject, email, role, status,
                        trading_mode, created_at, updated_at, last_seen_at
                    ) VALUES (?, NULL, ?, 'admin', 'active', 'disabled', ?, ?, NULL)
                    """,
                    (str(uuid4()), email, now, now),
                )
            self.connection.commit()

    def create_user(
        self,
        email: str,
        *,
        role: Role = Role.RESEARCHER,
        status: AccountStatus = AccountStatus.ACTIVE,
        trading_mode: TradingMode = TradingMode.DISABLED,
        actor_user_id: str | None = None,
    ) -> AuthUser:
        normalized = normalize_email(email)
        if not normalized or "@" not in normalized:
            raise ValueError("a valid email is required")
        if role is not Role.TRADER and trading_mode is not TradingMode.DISABLED:
            raise ValueError("only trader accounts can enable trading modes")
        user_id = str(uuid4())
        now = _now()
        with self.lock:
            try:
                self.connection.execute(
                    """
                    INSERT INTO app_users(
                        user_id, access_subject, email, role, status,
                        trading_mode, created_at, updated_at, last_seen_at
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        user_id,
                        normalized,
                        role.value,
                        status.value,
                        trading_mode.value,
                        now,
                        now,
                    ),
                )
                self.connection.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError("email is already registered") from exc
        self.append_audit_event(
            "user.created",
            "user",
            actor_user_id=actor_user_id,
            subject_user_id=user_id,
            resource_id=user_id,
            details={
                "email": normalized,
                "role": role.value,
                "status": status.value,
                "trading_mode": trading_mode.value,
            },
        )
        user = self.user_by_email(normalized)
        if user is None:  # pragma: no cover - guarded by the successful insert
            raise RuntimeError("created user could not be loaded")
        return user

    def update_user(
        self,
        user_id: str,
        *,
        role: Role,
        status: AccountStatus,
        trading_mode: TradingMode,
        actor_user_id: str | None = None,
    ) -> AuthUser:
        if role is not Role.TRADER and trading_mode is not TradingMode.DISABLED:
            raise ValueError("only trader accounts can enable trading modes")
        with self.lock:
            existing = self.connection.execute(
                "SELECT * FROM app_users WHERE user_id=?", (user_id,)
            ).fetchone()
            if existing is None:
                raise ValueError("platform user was not found")
            self.connection.execute(
                "UPDATE app_users SET role=?, status=?, trading_mode=?, "
                "updated_at=? WHERE user_id=?",
                (role.value, status.value, trading_mode.value, _now(), user_id),
            )
            self.connection.commit()
        self.append_audit_event(
            "user.updated",
            "user",
            actor_user_id=actor_user_id,
            subject_user_id=user_id,
            resource_id=user_id,
            details={
                "role": role.value,
                "status": status.value,
                "trading_mode": trading_mode.value,
            },
        )
        updated = self.user_by_id(user_id)
        if updated is None:  # pragma: no cover - guarded by the update
            raise RuntimeError("updated user could not be loaded")
        return updated

    def resolve_identity(self, identity: AccessIdentity) -> AuthUser | None:
        email = normalize_email(identity.email) if identity.email else None
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM app_users WHERE access_subject=?",
                (identity.subject,),
            ).fetchone()
            if row is None and email:
                candidate = self.connection.execute(
                    "SELECT * FROM app_users WHERE email=?", (email,)
                ).fetchone()
                if candidate and candidate["access_subject"] in (
                    None,
                    identity.subject,
                ):
                    self.connection.execute(
                        "UPDATE app_users SET access_subject=?, last_seen_at=?, "
                        "updated_at=? WHERE user_id=?",
                        (identity.subject, _now(), _now(), candidate["user_id"]),
                    )
                    self.connection.commit()
                    row = self.connection.execute(
                        "SELECT * FROM app_users WHERE user_id=?",
                        (candidate["user_id"],),
                    ).fetchone()
            elif row is not None:
                self.connection.execute(
                    "UPDATE app_users SET last_seen_at=? WHERE user_id=?",
                    (_now(), row["user_id"]),
                )
                self.connection.commit()
        return self._to_user(row) if row else None

    def user_by_email(self, email: str) -> AuthUser | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM app_users WHERE email=?",
                (normalize_email(email),),
            ).fetchone()
        return self._to_user(row) if row else None

    def user_by_id(self, user_id: str) -> AuthUser | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM app_users WHERE user_id=?", (user_id,)
            ).fetchone()
        return self._to_user(row) if row else None

    def users(self) -> list[AuthUser]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM app_users ORDER BY created_at, email"
            ).fetchall()
        return [self._to_user(row) for row in rows]

    def permissions_for_role(self, role: Role) -> tuple[str, ...]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT permission_key FROM role_permissions WHERE role=? "
                "ORDER BY permission_key",
                (role.value,),
            ).fetchall()
        return tuple(row["permission_key"] for row in rows)

    def append_audit_event(
        self,
        action: str,
        resource_type: str,
        *,
        actor_user_id: str | None = None,
        subject_user_id: str | None = None,
        resource_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> str:
        event_id = str(uuid4())
        with self.lock:
            self.connection.execute(
                "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event_id,
                    actor_user_id,
                    subject_user_id,
                    action,
                    resource_type,
                    resource_id,
                    json.dumps(details or {}, ensure_ascii=False, sort_keys=True),
                    _now(),
                ),
            )
            self.connection.commit()
        return event_id

    def audit_events(self) -> list[dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at, event_id"
            ).fetchall()
        return [
            {
                **dict(row),
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def _to_user(self, row: sqlite3.Row) -> AuthUser:
        role = Role(row["role"])
        return AuthUser(
            user_id=row["user_id"],
            access_subject=row["access_subject"],
            email=row["email"],
            role=role,
            status=AccountStatus(row["status"]),
            trading_mode=TradingMode(row["trading_mode"]),
            permissions=self.permissions_for_role(role),
        )

    def close(self) -> None:
        with self.lock:
            self.connection.close()
