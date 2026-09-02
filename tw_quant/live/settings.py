from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import os


def _split_origins(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class LiveSettings:
    mode: str = "mock"
    symbol: str = "TMF"
    contract: str = "TMFR1"
    db_path: str = "output/live_market.sqlite3"
    replay_csv: str = "data/mock_tmf_ticks.csv"
    replay_speed: float = 8.0
    heartbeat_seconds: float = 5.0
    allowed_origins: tuple[str, ...] = ("http://localhost:3000",)
    shioaji_api_key: str | None = None
    shioaji_secret_key: str | None = None
    shioaji_production: bool = False
    holidays: frozenset[date] = frozenset()
    access_mode: str = "disabled"
    cloudflare_access_team_domain: str | None = None
    cloudflare_access_audience: str | None = None

    @classmethod
    def from_env(cls) -> "LiveSettings":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass
        mode = os.getenv("MARKET_MODE", "mock").lower().strip()
        if mode not in {"mock", "shioaji"}:
            raise ValueError("MARKET_MODE must be mock or shioaji")
        access_mode = os.getenv("MARKET_ACCESS_MODE", "disabled").lower().strip()
        if access_mode not in {"disabled", "cloudflare"}:
            raise ValueError("MARKET_ACCESS_MODE must be disabled or cloudflare")
        return cls(
            mode=mode,
            symbol=os.getenv("MARKET_SYMBOL", "TMF").upper(),
            contract=os.getenv("MARKET_CONTRACT", "TMFR1").upper(),
            db_path=os.getenv("MARKET_DB_PATH", "output/live_market.sqlite3"),
            replay_csv=os.getenv("MARKET_REPLAY_CSV", "data/mock_tmf_ticks.csv"),
            replay_speed=float(os.getenv("MARKET_REPLAY_SPEED", "8")),
            heartbeat_seconds=float(os.getenv("MARKET_HEARTBEAT_SECONDS", "5")),
            allowed_origins=_split_origins(
                os.getenv("MARKET_ALLOWED_ORIGINS", "http://localhost:3000")
            ),
            shioaji_api_key=os.getenv("SJ_API_KEY"),
            shioaji_secret_key=os.getenv("SJ_SEC_KEY"),
            shioaji_production=os.getenv("SJ_PRODUCTION", "false").lower()
            in {"1", "true", "yes"},
            holidays=frozenset(
                date.fromisoformat(item.strip())
                for item in os.getenv("MARKET_HOLIDAYS", "").split(",")
                if item.strip()
            ),
            access_mode=access_mode,
            cloudflare_access_team_domain=os.getenv("CF_ACCESS_TEAM_DOMAIN"),
            cloudflare_access_audience=os.getenv("CF_ACCESS_AUD"),
        )

    def validate(self) -> None:
        if self.replay_speed <= 0 or self.heartbeat_seconds <= 0:
            raise ValueError("replay speed and heartbeat interval must be positive")
        if self.mode == "shioaji" and not (
            self.shioaji_api_key and self.shioaji_secret_key
        ):
            raise ValueError("SJ_API_KEY and SJ_SEC_KEY are required in shioaji mode")
        if self.access_mode == "cloudflare" and not (
            self.cloudflare_access_team_domain and self.cloudflare_access_audience
        ):
            raise ValueError(
                "CF_ACCESS_TEAM_DOMAIN and CF_ACCESS_AUD are required in cloudflare mode"
            )
