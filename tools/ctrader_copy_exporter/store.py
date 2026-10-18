import csv
import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TRADE_COLUMNS = (
    "trade_id",
    "opened_at_utc",
    "displayed_timestamp",
    "ui_timezone",
    "interval_id",
    "raw_fields_json",
)


class ExportStore:
    """SQLite-backed checkpoints and trade IDs with a recoverable CSV projection."""

    def __init__(self, directory: Path, run_id: str) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.database_path = directory / "export.sqlite3"
        self.csv_path = directory / "trades.csv"
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        self.rebuild_csv()

    def close(self) -> None:
        self.connection.close()

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                run_id TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                opened_at_utc TEXT NOT NULL,
                displayed_timestamp TEXT NOT NULL,
                ui_timezone TEXT NOT NULL,
                interval_id TEXT NOT NULL,
                raw_fields_json TEXT NOT NULL,
                PRIMARY KEY (run_id, trade_id)
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id TEXT NOT NULL,
                interval_id TEXT NOT NULL,
                parent_id TEXT,
                granularity TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL,
                ui_start TEXT NOT NULL,
                ui_end TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                scroll_state_json TEXT NOT NULL DEFAULT '{}',
                unique_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                refresh_count INTEGER NOT NULL DEFAULT 0,
                verification TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, interval_id)
            );
            CREATE TABLE IF NOT EXISTS batches (
                batch_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                interval_id TEXT NOT NULL,
                inserted_count INTEGER NOT NULL,
                duplicate_count INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                committed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS manifests (
                run_id TEXT PRIMARY KEY,
                content_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def save_manifest(self, manifest: dict[str, Any]) -> None:
        self.connection.execute(
            """
            INSERT INTO manifests(run_id, content_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                content_json=excluded.content_json, updated_at=excluded.updated_at
            """,
            (self.run_id, json.dumps(manifest, sort_keys=True), _now()),
        )
        self.connection.commit()

    def manifest(self) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT content_json FROM manifests WHERE run_id=?",
            (self.run_id,),
        ).fetchone()
        return json.loads(row["content_json"]) if row else None

    def checkpoint(self, interval: Any, status: str, **updates: Any) -> None:
        current = self.connection.execute(
            "SELECT attempts, unique_count, duplicate_count, refresh_count, scroll_state_json "
            "FROM checkpoints "
            "WHERE run_id=? AND interval_id=?",
            (self.run_id, interval.interval_id),
        ).fetchone()
        attempts = (current["attempts"] if current else 0) + int(status == "in_progress")
        counts = {
            "unique_count": updates.get("unique_count", current["unique_count"] if current else 0),
            "duplicate_count": updates.get(
                "duplicate_count", current["duplicate_count"] if current else 0
            ),
            "refresh_count": updates.get(
                "refresh_count", current["refresh_count"] if current else 0
            ),
        }
        checkpoint_state = (
            json.loads(current["scroll_state_json"])
            if current and current["scroll_state_json"]
            else {}
        )
        if "ui_filter" in updates:
            checkpoint_state["ui_filter"] = updates["ui_filter"]
        if "scroll_state" in updates:
            checkpoint_state["scroll_state"] = updates["scroll_state"]
        self.connection.execute(
            """
            INSERT INTO checkpoints(
                run_id, interval_id, parent_id, granularity, start_utc, end_utc,
                ui_start, ui_end, status, attempts, scroll_state_json, unique_count,
                duplicate_count, refresh_count, verification, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, interval_id) DO UPDATE SET
                status=excluded.status, attempts=excluded.attempts,
                scroll_state_json=excluded.scroll_state_json,
                unique_count=excluded.unique_count,
                duplicate_count=excluded.duplicate_count,
                refresh_count=excluded.refresh_count,
                verification=excluded.verification, updated_at=excluded.updated_at
            """,
            (
                self.run_id,
                interval.interval_id,
                interval.parent_id,
                interval.granularity,
                interval.start.astimezone(UTC).isoformat(),
                interval.end.astimezone(UTC).isoformat(),
                interval.ui_start,
                interval.ui_end,
                status,
                attempts,
                json.dumps(checkpoint_state, sort_keys=True),
                counts["unique_count"],
                counts["duplicate_count"],
                counts["refresh_count"],
                updates.get("verification"),
                _now(),
            ),
        )
        self.connection.commit()

    def checkpoint_status(self, interval_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT status FROM checkpoints WHERE run_id=? AND interval_id=?",
            (self.run_id, interval_id),
        ).fetchone()
        return row["status"] if row else None

    def upsert_batch(self, interval: Any, trades: list[dict[str, Any]]) -> tuple[int, int]:
        """Commit rows/checkpoint atomically, then rebuild durable CSV from SQLite."""
        inserted = 0
        duplicates = 0
        serialized = []
        for trade in trades:
            record = (
                self.run_id,
                trade["trade_id"],
                trade["opened_at_utc"],
                trade["displayed_timestamp"],
                trade["ui_timezone"],
                interval.interval_id,
                json.dumps(trade["raw_fields"], sort_keys=True, ensure_ascii=False),
            )
            serialized.append(record)
        checksum = hashlib.sha256(
            "\n".join(f"{row[1]}:{row[2]}:{row[6]}" for row in serialized).encode()
        ).hexdigest()
        batch_id = uuid.uuid4().hex
        with self.connection:
            for record in serialized:
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO trades(
                        run_id, trade_id, opened_at_utc, displayed_timestamp,
                        ui_timezone, interval_id, raw_fields_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    record,
                )
                inserted += cursor.rowcount
                duplicates += int(cursor.rowcount == 0)
            self.connection.execute(
                """
                INSERT OR IGNORE INTO batches(
                    batch_id, run_id, interval_id, inserted_count,
                    duplicate_count, checksum, committed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    self.run_id,
                    interval.interval_id,
                    inserted,
                    duplicates,
                    checksum,
                    _now(),
                ),
            )
            total = self.unique_count()
            self.connection.execute(
                """
                INSERT INTO checkpoints(
                    run_id, interval_id, parent_id, granularity, start_utc, end_utc,
                    ui_start, ui_end, status, attempts, unique_count,
                    duplicate_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in_progress', 0, ?, ?, ?)
                ON CONFLICT(run_id, interval_id) DO UPDATE SET
                    unique_count=excluded.unique_count,
                    duplicate_count=checkpoints.duplicate_count + excluded.duplicate_count,
                    updated_at=excluded.updated_at
                """,
                (
                    self.run_id,
                    interval.interval_id,
                    interval.parent_id,
                    interval.granularity,
                    interval.start.astimezone(UTC).isoformat(),
                    interval.end.astimezone(UTC).isoformat(),
                    interval.ui_start,
                    interval.ui_end,
                    total,
                    duplicates,
                    _now(),
                ),
            )
        self.rebuild_csv()
        return inserted, duplicates

    def contains_trade(self, trade_id: str) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM trades WHERE run_id=? AND trade_id=?",
                (self.run_id, trade_id),
            ).fetchone()
            is not None
        )

    def trade_timestamp(self, trade_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT opened_at_utc FROM trades WHERE run_id=? AND trade_id=?",
            (self.run_id, trade_id),
        ).fetchone()
        return row["opened_at_utc"] if row else None

    def unique_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM trades WHERE run_id=?",
            (self.run_id,),
        ).fetchone()
        return int(row["count"])

    def trade_ids(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT trade_id FROM trades WHERE run_id=?",
            (self.run_id,),
        )
        return {row["trade_id"] for row in rows}

    def trade_ids_in_range(self, start: datetime, end: datetime) -> set[str]:
        rows = self.connection.execute(
            """
            SELECT trade_id FROM trades
            WHERE run_id=? AND opened_at_utc >= ? AND opened_at_utc < ?
            """,
            (self.run_id, start.astimezone(UTC).isoformat(), end.astimezone(UTC).isoformat()),
        )
        return {row["trade_id"] for row in rows}

    def batch_count(self) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM batches WHERE run_id=?",
            (self.run_id,),
        ).fetchone()
        return int(row["count"])

    def checkpoints(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM checkpoints WHERE run_id=? ORDER BY start_utc",
            (self.run_id,),
        )
        return [dict(row) for row in rows]

    def rebuild_csv(self) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="trades-", suffix=".tmp", dir=self.directory
        )
        try:
            with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as output:
                writer = csv.DictWriter(output, fieldnames=TRADE_COLUMNS)
                writer.writeheader()
                rows = self.connection.execute(
                    """
                    SELECT trade_id, opened_at_utc, displayed_timestamp, ui_timezone,
                           interval_id, raw_fields_json
                    FROM trades WHERE run_id=? ORDER BY opened_at_utc, trade_id
                    """,
                    (self.run_id,),
                )
                for row in rows:
                    writer.writerow(dict(row))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_name, self.csv_path)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise


def _now() -> str:
    return datetime.now(UTC).isoformat()
