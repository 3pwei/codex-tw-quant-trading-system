from __future__ import annotations

import sqlite3
import json
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Protocol
from uuid import uuid4

from ..market import KBar


class StrategyPurgeError(ValueError):
    """Raised when a composite strategy cannot be permanently deleted."""


class StrategyReferencedError(StrategyPurgeError):
    def __init__(self, references: dict[str, int]):
        self.references = references
        labels = ", ".join(
            f"{strategy_id} ({count} 筆)"
            for strategy_id, count in references.items()
        )
        super().__init__(f"策略已有回測引用，禁止永久刪除：{labels}")


class BarRepository(Protocol):
    def save(self, bar: KBar) -> None: ...
    def latest(self, symbol: str, limit: int) -> list[KBar]: ...
    def latest_forming(self, symbol: str) -> KBar | None: ...
    def date_bounds(self, symbol: str) -> tuple[date | None, date | None]: ...
    def between_trading_dates(
        self, symbol: str, start: date, end: date
    ) -> list[KBar]: ...
    def tick_seen(self, key: str) -> bool: ...
    def remember_tick(self, key: str, exchange_time: datetime) -> None: ...
    def purge_backfill(self, symbol: str, contract: str) -> None: ...
    def strategy_parameters(self) -> dict[str, dict[str, object]]: ...
    def save_strategy_parameters(
        self, strategy: str, parameters: dict[str, int | float]
    ) -> None: ...
    def composite_strategies(self) -> list[dict[str, object]]: ...
    def archived_composite_strategies(self) -> list[dict[str, object]]: ...
    def composite_strategy_versions(
        self, strategy_id: str
    ) -> list[dict[str, object]]: ...
    def composite_strategy(
        self, strategy_id: str, version: int | None = None
    ) -> dict[str, object] | None: ...
    def save_composite_strategy(
        self, strategy_id: str, definition: dict[str, object]
    ) -> dict[str, object]: ...
    def composite_strategy_archived(self, strategy_id: str) -> bool: ...
    def archive_composite_strategy(self, strategy_id: str) -> dict[str, object]: ...
    def purge_archived_composite_strategies(
        self, strategy_ids: list[str]
    ) -> dict[str, object]: ...
    def save_backtest_run(
        self,
        result: dict[str, object],
        strategy_kind: str,
        strategy_key: str,
        strategy_version: int | None,
        strategy_snapshot: dict[str, object],
    ) -> dict[str, object]: ...
    def backtest_runs(
        self, limit: int, offset: int, strategy_key: str | None = None
    ) -> list[dict[str, object]]: ...
    def backtest_run(self, run_id: str) -> dict[str, object] | None: ...
    def close(self) -> None: ...


class SQLiteBarRepository:
    def __init__(self, path: str | Path):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(target, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = Lock()
        self.connection.execute("PRAGMA foreign_keys=ON")
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
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS strategy_parameters (
                strategy TEXT PRIMARY KEY,
                parameters_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS composite_strategies (
                strategy_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                name TEXT NOT NULL,
                definition_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(strategy_id, version)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS archived_composite_strategies (
                strategy_id TEXT PRIMARY KEY,
                archived_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()
        self._ensure_backtest_schema()

    def _ensure_backtest_schema(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(backtest_runs)"
            ).fetchall()
        }
        legacy_rows: list[sqlite3.Row] = []
        if columns and "strategy_kind" not in columns:
            legacy_rows = self.connection.execute(
                "SELECT * FROM backtest_runs"
            ).fetchall()
            self.connection.execute(
                "ALTER TABLE backtest_runs RENAME TO backtest_runs_legacy"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                run_id TEXT PRIMARY KEY,
                strategy_kind TEXT NOT NULL,
                strategy_key TEXT NOT NULL,
                strategy_version INTEGER,
                strategy_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                strategy_snapshot_json TEXT NOT NULL,
                result_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                CHECK(strategy_kind IN ('atomic', 'composite')),
                CHECK(
                    (strategy_kind='atomic' AND strategy_version IS NULL)
                    OR
                    (strategy_kind='composite' AND strategy_version IS NOT NULL)
                ),
                FOREIGN KEY(strategy_key, strategy_version)
                    REFERENCES composite_strategies(strategy_id, version)
                    ON DELETE RESTRICT
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_runs_created "
            "ON backtest_runs(created_at DESC)"
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy "
            "ON backtest_runs(strategy_key, strategy_version)"
        )
        for row in legacy_rows:
            result = {
                "metadata": {
                    "symbol": "TMF",
                    "strategy": row["strategy_id"],
                    "strategy_key": row["strategy_id"],
                    "strategy_version": row["strategy_version"],
                    "interval": "多週期",
                    "date_range": f"{row['start_date']} ～ {row['end_date']}",
                },
                "config": json.loads(row["parameters_json"]),
                "summary": json.loads(row["metrics_json"]),
                "trades": [],
                "equity": [],
            }
            self.connection.execute(
                "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["run_id"], "composite", row["strategy_id"],
                    row["strategy_version"], row["strategy_id"], "TMF", "multi",
                    row["start_date"], row["end_date"],
                    row["strategy_snapshot_json"],
                    json.dumps(result, ensure_ascii=False), row["status"],
                    row["created_at"],
                ),
            )
        if legacy_rows or "strategy_kind" not in columns and columns:
            self.connection.execute("DROP TABLE IF EXISTS backtest_runs_legacy")
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

    def date_bounds(self, symbol: str) -> tuple[date | None, date | None]:
        with self.lock:
            row = self.connection.execute(
                "SELECT MIN(trading_date) AS first_date, "
                "MAX(trading_date) AS last_date FROM minute_bars "
                "WHERE symbol=? AND status='closed'",
                (symbol,),
            ).fetchone()
        first = date.fromisoformat(row["first_date"]) if row["first_date"] else None
        last = date.fromisoformat(row["last_date"]) if row["last_date"] else None
        return first, last

    def between_trading_dates(
        self, symbol: str, start: date, end: date
    ) -> list[KBar]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM minute_bars WHERE symbol=? AND status='closed' "
                "AND trading_date BETWEEN ? AND ? ORDER BY time",
                (symbol, start.isoformat(), end.isoformat()),
            ).fetchall()
        return [self._to_bar(row) for row in rows]

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

    def strategy_parameters(self) -> dict[str, dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT strategy, parameters_json FROM strategy_parameters"
            ).fetchall()
        return {
            row["strategy"]: json.loads(row["parameters_json"])
            for row in rows
        }

    def save_strategy_parameters(
        self, strategy: str, parameters: dict[str, int | float]
    ) -> None:
        payload = json.dumps(parameters, ensure_ascii=False, sort_keys=True)
        updated_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO strategy_parameters(strategy, parameters_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(strategy) DO UPDATE SET
                    parameters_json=excluded.parameters_json,
                    updated_at=excluded.updated_at
                """,
                (strategy, payload, updated_at),
            )
            self.connection.commit()

    @staticmethod
    def _composite_row(row: sqlite3.Row) -> dict[str, object]:
        return {
            "id": row["strategy_id"],
            "version": row["version"],
            "name": row["name"],
            "definition": json.loads(row["definition_json"]),
            "created_at": row["created_at"],
        }

    def composite_strategies(self) -> list[dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT item.* FROM composite_strategies item
                JOIN (
                    SELECT strategy_id, MAX(version) AS version
                    FROM composite_strategies GROUP BY strategy_id
                ) latest ON latest.strategy_id=item.strategy_id
                    AND latest.version=item.version
                WHERE NOT EXISTS (
                    SELECT 1 FROM archived_composite_strategies archived
                    WHERE archived.strategy_id=item.strategy_id
                )
                ORDER BY item.created_at DESC
                """
            ).fetchall()
        return [self._composite_row(row) for row in rows]

    def archived_composite_strategies(self) -> list[dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT item.*, archived.archived_at
                FROM archived_composite_strategies archived
                JOIN composite_strategies item
                  ON item.strategy_id=archived.strategy_id
                JOIN (
                    SELECT strategy_id, MAX(version) AS version
                    FROM composite_strategies GROUP BY strategy_id
                ) latest ON latest.strategy_id=item.strategy_id
                    AND latest.version=item.version
                ORDER BY archived.archived_at DESC
                """
            ).fetchall()
        return [
            {**self._composite_row(row), "archived_at": row["archived_at"]}
            for row in rows
        ]

    def composite_strategy_versions(
        self, strategy_id: str
    ) -> list[dict[str, object]]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM composite_strategies WHERE strategy_id=? "
                "ORDER BY version DESC",
                (strategy_id,),
            ).fetchall()
        return [self._composite_row(row) for row in rows]

    def composite_strategy(
        self, strategy_id: str, version: int | None = None
    ) -> dict[str, object] | None:
        with self.lock:
            if version is None:
                row = self.connection.execute(
                    "SELECT * FROM composite_strategies WHERE strategy_id=? "
                    "ORDER BY version DESC LIMIT 1",
                    (strategy_id,),
                ).fetchone()
            else:
                row = self.connection.execute(
                    "SELECT * FROM composite_strategies "
                    "WHERE strategy_id=? AND version=?",
                    (strategy_id, version),
                ).fetchone()
        return self._composite_row(row) if row else None

    def save_composite_strategy(
        self, strategy_id: str, definition: dict[str, object]
    ) -> dict[str, object]:
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        payload = json.dumps(definition, ensure_ascii=False, sort_keys=True)
        with self.lock:
            archived = self.connection.execute(
                "SELECT 1 FROM archived_composite_strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
            if archived:
                raise ValueError("已封存的組合策略不可修改")
            row = self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version "
                "FROM composite_strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
            version = int(row["version"]) + 1
            self.connection.execute(
                "INSERT INTO composite_strategies VALUES (?, ?, ?, ?, ?)",
                (strategy_id, version, definition["name"], payload, created_at),
            )
            self.connection.commit()
        return {
            "id": strategy_id,
            "version": version,
            "name": definition["name"],
            "definition": definition,
            "created_at": created_at,
        }

    def composite_strategy_archived(self, strategy_id: str) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT 1 FROM archived_composite_strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
        return row is not None

    def archive_composite_strategy(self, strategy_id: str) -> dict[str, object]:
        archived_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.lock:
            exists = self.connection.execute(
                "SELECT 1 FROM composite_strategies WHERE strategy_id=? LIMIT 1",
                (strategy_id,),
            ).fetchone()
            if not exists:
                raise ValueError("找不到組合策略")
            self.connection.execute(
                "INSERT INTO archived_composite_strategies VALUES (?, ?) "
                "ON CONFLICT(strategy_id) DO UPDATE SET archived_at=excluded.archived_at",
                (strategy_id, archived_at),
            )
            self.connection.commit()
        return {"id": strategy_id, "archived_at": archived_at}

    def purge_archived_composite_strategies(
        self, strategy_ids: list[str]
    ) -> dict[str, object]:
        ids = list(dict.fromkeys(item.strip() for item in strategy_ids if item.strip()))
        if not ids:
            raise StrategyPurgeError("至少需要選擇一個封存策略")
        placeholders = ",".join("?" for _ in ids)
        with self.lock:
            archived_rows = self.connection.execute(
                f"SELECT strategy_id FROM archived_composite_strategies "
                f"WHERE strategy_id IN ({placeholders})",
                ids,
            ).fetchall()
            archived = {row["strategy_id"] for row in archived_rows}
            not_archived = [item for item in ids if item not in archived]
            if not_archived:
                raise StrategyPurgeError(
                    "只有封存策略可以永久刪除：" + ", ".join(not_archived)
                )
            reference_rows = self.connection.execute(
                f"SELECT strategy_key, COUNT(*) AS count FROM backtest_runs "
                f"WHERE strategy_kind='composite' "
                f"AND strategy_key IN ({placeholders}) GROUP BY strategy_key",
                ids,
            ).fetchall()
            references = {
                row["strategy_key"]: int(row["count"]) for row in reference_rows
            }
            if references:
                raise StrategyReferencedError(references)
            version_rows = self.connection.execute(
                f"SELECT strategy_id, COUNT(*) AS count FROM composite_strategies "
                f"WHERE strategy_id IN ({placeholders}) GROUP BY strategy_id",
                ids,
            ).fetchall()
            version_count = sum(int(row["count"]) for row in version_rows)
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                self.connection.execute(
                    f"DELETE FROM archived_composite_strategies "
                    f"WHERE strategy_id IN ({placeholders})",
                    ids,
                )
                self.connection.execute(
                    f"DELETE FROM composite_strategies "
                    f"WHERE strategy_id IN ({placeholders})",
                    ids,
                )
                self.connection.commit()
            except sqlite3.IntegrityError as exc:
                self.connection.rollback()
                raise StrategyReferencedError(
                    {strategy_id: 1 for strategy_id in ids}
                ) from exc
            except Exception:
                self.connection.rollback()
                raise
        return {
            "purged_strategy_ids": ids,
            "deleted_strategies": len(ids),
            "deleted_versions": version_count,
        }

    @staticmethod
    def _backtest_row(row: sqlite3.Row, *, detail: bool = False) -> dict[str, object]:
        result = json.loads(row["result_json"])
        summary = result.get("summary", {})
        item: dict[str, object] = {
            "run_id": row["run_id"],
            "strategy_kind": row["strategy_kind"],
            "strategy_key": row["strategy_key"],
            "strategy_version": row["strategy_version"],
            "strategy_name": row["strategy_name"],
            "symbol": row["symbol"],
            "interval": row["interval"],
            "start_date": row["start_date"],
            "end_date": row["end_date"],
            "status": row["status"],
            "created_at": row["created_at"],
            "trade_count": len(result.get("trades", [])),
            "summary": summary,
        }
        if detail:
            item["strategy_snapshot"] = json.loads(row["strategy_snapshot_json"])
            item["result"] = result
        return item

    def save_backtest_run(
        self,
        result: dict[str, object],
        strategy_kind: str,
        strategy_key: str,
        strategy_version: int | None,
        strategy_snapshot: dict[str, object],
    ) -> dict[str, object]:
        if strategy_kind not in {"atomic", "composite"}:
            raise ValueError("unsupported strategy kind")
        metadata = result["metadata"]
        date_range = str(metadata["date_range"]).split(" ～ ")
        if len(date_range) != 2:
            raise ValueError("invalid backtest date range")
        stored_result = {
            key: value for key, value in result.items() if key != "bars"
        }
        run_id = uuid4().hex
        created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with self.lock:
            self.connection.execute(
                "INSERT INTO backtest_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, strategy_kind, strategy_key, strategy_version,
                    metadata["strategy"], metadata["symbol"],
                    metadata.get("interval_key", metadata["interval"]),
                    date_range[0], date_range[1],
                    json.dumps(strategy_snapshot, ensure_ascii=False, sort_keys=True),
                    json.dumps(stored_result, ensure_ascii=False),
                    "completed", created_at,
                ),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._backtest_row(row, detail=True)

    def backtest_runs(
        self, limit: int, offset: int, strategy_key: str | None = None
    ) -> list[dict[str, object]]:
        sql = "SELECT * FROM backtest_runs"
        parameters: list[object] = []
        if strategy_key:
            sql += " WHERE strategy_key=?"
            parameters.append(strategy_key)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        parameters.extend([limit, offset])
        with self.lock:
            rows = self.connection.execute(sql, parameters).fetchall()
        return [self._backtest_row(row) for row in rows]

    def backtest_run(self, run_id: str) -> dict[str, object] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM backtest_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._backtest_row(row, detail=True) if row else None

    def close(self) -> None:
        with self.lock:
            self.connection.close()
