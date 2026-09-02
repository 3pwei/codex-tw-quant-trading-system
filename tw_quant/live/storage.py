from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol

from .models import KBar


class BarRepository(Protocol):
    def save(self, bar: KBar) -> None: ...
    def latest(self, symbol: str, limit: int) -> list[KBar]: ...
    def latest_forming(self, symbol: str) -> KBar | None: ...
    def tick_seen(self, key: str) -> bool: ...
    def remember_tick(self, key: str, exchange_time: datetime) -> None: ...
    def purge_backfill(self, symbol: str, contract: str) -> None: ...
    def close(self) -> None: ...


class SQLiteBarRepository:
    def __init__(self, path: str | Path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(target, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS minute_bars (
                symbol TEXT NOT NULL,
                contract TEXT NOT NULL,
                time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                status TEXT NOT NULL,
                session TEXT NOT NULL,
                trading_date TEXT NOT NULL,
                first_tick_time TEXT NOT NULL,
                last_tick_time TEXT NOT NULL,
                exchange_time TEXT NOT NULL,
                received_time TEXT NOT NULL,
                latency_ms REAL NOT NULL,
                no_trade INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(symbol, contract, time)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_ticks (
                dedup_key TEXT PRIMARY KEY,
                exchange_time TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def save(self, bar: KBar) -> None:
        values = (
            bar.symbol, bar.contract, bar.time.isoformat(), bar.open, bar.high,
            bar.low, bar.close, bar.volume, bar.status, bar.session,
            bar.trading_date.isoformat(), bar.first_tick_time.isoformat(),
            bar.last_tick_time.isoformat(), bar.exchange_time.isoformat(),
            bar.received_time.isoformat(), bar.latency_ms, int(bar.no_trade),
        )
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO minute_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, contract, time) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume,
                    status=excluded.status, session=excluded.session,
                    trading_date=excluded.trading_date,
                    first_tick_time=excluded.first_tick_time,
                    last_tick_time=excluded.last_tick_time,
                    exchange_time=excluded.exchange_time,
                    received_time=excluded.received_time,
                    latency_ms=excluded.latency_ms,
                    no_trade=excluded.no_trade
                """,
                values,
            )
            self.connection.commit()

    @staticmethod
    def _to_bar(row: sqlite3.Row) -> KBar:
        return KBar(
            symbol=row["symbol"], contract=row["contract"],
            time=datetime.fromisoformat(row["time"]), open=row["open"],
            high=row["high"], low=row["low"], close=row["close"],
            volume=row["volume"], status=row["status"], session=row["session"],
            trading_date=date.fromisoformat(row["trading_date"]),
            first_tick_time=datetime.fromisoformat(row["first_tick_time"]),
            last_tick_time=datetime.fromisoformat(row["last_tick_time"]),
            exchange_time=datetime.fromisoformat(row["exchange_time"]),
            received_time=datetime.fromisoformat(row["received_time"]),
            latency_ms=row["latency_ms"], no_trade=bool(row["no_trade"]),
        )

    def latest(self, symbol: str, limit: int) -> list[KBar]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM minute_bars WHERE symbol=? ORDER BY time DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        return [self._to_bar(row) for row in reversed(rows)]

    def latest_forming(self, symbol: str) -> KBar | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM minute_bars WHERE symbol=? AND status='forming' "
                "ORDER BY time DESC LIMIT 1",
                (symbol,),
            ).fetchone()
        return self._to_bar(row) if row else None

    def tick_seen(self, key: str) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT 1 FROM processed_ticks WHERE dedup_key=?", (key,)
            ).fetchone()
        return row is not None

    def remember_tick(self, key: str, exchange_time: datetime) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR IGNORE INTO processed_ticks VALUES (?,?)",
                (key, exchange_time.isoformat()),
            )
            self.connection.commit()

    def purge_backfill(self, symbol: str, contract: str) -> None:
        """Remove only bars generated by the historical backfill adapter.

        Backfilled bars use exact minute boundaries, a synthetic 59.999 second
        last-tick marker, zero latency, and closed status. Tick-aggregated bars
        retain their real first/last Tick timestamps and are not matched.
        """
        with self.lock:
            rows = self.connection.execute(
                "SELECT time, first_tick_time, last_tick_time FROM minute_bars "
                "WHERE symbol=? AND contract=? AND status='closed' "
                "AND latency_ms=0",
                (symbol, contract),
            ).fetchall()
            keys = []
            for row in rows:
                bar_time = datetime.fromisoformat(row["time"])
                first = datetime.fromisoformat(row["first_tick_time"])
                last = datetime.fromisoformat(row["last_tick_time"])
                if first == bar_time and last == bar_time.replace(
                    second=59, microsecond=999000
                ):
                    keys.append((symbol, contract, row["time"]))
            self.connection.executemany(
                "DELETE FROM minute_bars WHERE symbol=? AND contract=? AND time=?",
                keys,
            )
            self.connection.commit()

    def close(self) -> None:
        with self.lock:
            self.connection.close()
