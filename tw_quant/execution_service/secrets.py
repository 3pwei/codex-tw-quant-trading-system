from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import stat
from typing import Mapping, Protocol


class SecretConfigurationError(ValueError):
    """Raised without embedding any secret value in its message."""

    def __init__(self, issue_codes: tuple[str, ...]):
        self.issue_codes = issue_codes
        super().__init__("invalid live broker secret configuration: " + ",".join(issue_codes))


@dataclass(frozen=True)
class LiveBrokerSecrets:
    api_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    account_id: str = field(repr=False)
    ca_cert_path: Path = field(repr=False)
    ca_password: str = field(repr=False)
    allowed_account_ids: frozenset[str] = field(repr=False)


class LiveBrokerSecretLoader(Protocol):
    def load(self) -> LiveBrokerSecrets: ...


@dataclass(frozen=True)
class EnvironmentLiveBrokerSecretLoader:
    """Read live credentials only inside the execution service container."""

    env: Mapping[str, str] | None = None

    def load(self) -> LiveBrokerSecrets:
        values = os.environ if self.env is None else self.env
        raw = {
            "missing_api_key": values.get("SJ_API_KEY", "").strip(),
            "missing_secret_key": values.get("SJ_SECRET_KEY", "").strip(),
            "missing_account_id": values.get("LIVE_BROKER_ACCOUNT_ID", "").strip(),
            "missing_ca_certificate": values.get("CA_CERT_PATH", "").strip(),
            "missing_ca_password": values.get("CA_PASSWORD", "").strip(),
            "missing_account_allowlist": values.get(
                "LIVE_ALLOWED_ACCOUNT_IDS", ""
            ).strip(),
        }
        missing = tuple(code for code, value in raw.items() if not value)
        if missing:
            raise SecretConfigurationError(missing)

        account_id = raw["missing_account_id"]
        allowed = frozenset(
            item.strip()
            for item in raw["missing_account_allowlist"].split(",")
            if item.strip()
        )
        issues: list[str] = []
        if account_id not in allowed:
            issues.append("account_not_allowlisted")

        ca_path = Path(raw["missing_ca_certificate"])
        try:
            ca_stat = ca_path.stat()
        except OSError:
            issues.append("ca_certificate_unavailable")
        else:
            if not stat.S_ISREG(ca_stat.st_mode) or ca_path.is_symlink():
                issues.append("ca_certificate_not_regular_file")
            if stat.S_IMODE(ca_stat.st_mode) & 0o077:
                issues.append("ca_certificate_permissions_too_open")
        if issues:
            raise SecretConfigurationError(tuple(issues))

        return LiveBrokerSecrets(
            api_key=raw["missing_api_key"],
            secret_key=raw["missing_secret_key"],
            account_id=account_id,
            ca_cert_path=ca_path,
            ca_password=raw["missing_ca_password"],
            allowed_account_ids=allowed,
        )
