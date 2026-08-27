from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generic, Iterable, Mapping, TypeVar
import hashlib
import json
import sqlite3

from .atomic import StateFormatError, canonical_json
from .json_store import StateCodec

T = TypeVar("T")

CHECKPOINT_KEY = "checkpoint_state"


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int | None
    observed_at: datetime
    event_type: str
    payload: dict[str, Any]
    classification: str | None = None


class EventLog:
    """
    An append-only journal of what a live system did, in SQLite.

    Every decision worth explaining afterwards goes in here. ``SignalSink`` is the in-memory
    equivalent; this is the one that survives a restart.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def append(
        self, event_type: str, payload: Mapping[str, Any], *, observed_at: datetime | None = None
    ) -> int:
        cursor = self.connection.execute(
            "INSERT INTO events(observed_at, event_type, payload_json) VALUES (?, ?, ?)",
            (_iso(observed_at), str(event_type), canonical_json(dict(payload))),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def events(self, *, event_type: str | None = None, limit: int | None = None) -> list[Event]:
        query = "SELECT sequence, observed_at, event_type, payload_json FROM events"
        parameters: tuple[Any, ...] = ()
        if event_type is not None:
            query += " WHERE event_type = ?"
            parameters = (event_type,)
        query += " ORDER BY sequence"
        if limit is not None:
            query += f" LIMIT {int(limit)}"
        return [_event(row) for row in self.connection.execute(query, parameters)]

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def __enter__(self) -> "EventLog":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class EventStore(Generic[T]):
    """
    A journal and a state checkpoint that can never disagree.

    Events are written but not committed; ``checkpoint`` writes the state and commits both in one
    transaction. So after a crash the state you load is exactly the state that the events on disk
    add up to — never a checkpoint ahead of its journal, nor a journal whose events were already
    folded into a state that was never saved.

    Events are keyed by a hash of their own content, and inserted with ``INSERT OR IGNORE``, so
    replaying a poll that was already recorded adds nothing.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        codec: StateCodec[T] | None = None,
        identity: Mapping[str, str] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.codec = codec
        self.connection = sqlite3.connect(self.path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_key TEXT PRIMARY KEY,
                observed_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                classification TEXT,
                sequence INTEGER,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS events_observed_at
                ON events(observed_at, event_type);
            """
        )
        for key, value in (identity or {}).items():
            self._require_metadata(str(key), str(value))
        self.connection.commit()

    # ----- Journal ----- #

    def record(
        self,
        event_type: str,
        payload: Mapping[str, Any],
        *,
        observed_at: datetime | None = None,
        sequence: int | None = None,
        classification: str | None = None,
    ) -> bool:
        """Write one event, uncommitted. ``False`` means this exact event was already stored."""
        encoded = canonical_json(dict(payload))
        stamp = _iso(observed_at)
        event_key = hashlib.sha256(
            f"{event_type}\0{stamp}\0{encoded}".encode("utf-8")
        ).hexdigest()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO events(
                event_key, observed_at, event_type, classification, sequence, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (event_key, stamp, str(event_type), classification, sequence, encoded),
        )
        return cursor.rowcount > 0

    def events(self, *, event_type: str | None = None) -> list[Event]:
        query = (
            "SELECT sequence, observed_at, event_type, payload_json, classification FROM events"
        )
        parameters: tuple[Any, ...] = ()
        if event_type is not None:
            query += " WHERE event_type = ?"
            parameters = (event_type,)
        query += " ORDER BY observed_at, event_key"
        return [_event(row) for row in self.connection.execute(query, parameters)]

    def event_counts(self, *, classification: str | None = None) -> dict[str, int]:
        query = "SELECT event_type, COUNT(*) FROM events"
        parameters: tuple[Any, ...] = ()
        if classification is not None:
            query += " WHERE classification = ?"
            parameters = (classification,)
        return {
            str(row[0]): int(row[1])
            for row in self.connection.execute(query + " GROUP BY event_type", parameters)
        }

    # ----- Checkpoint ----- #

    def checkpoint(self, state: T) -> None:
        """Store the state and commit it together with every event written since the last one."""
        if self.codec is None:
            raise StateFormatError("EventStore needs a codec to checkpoint state")
        self.connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (CHECKPOINT_KEY, canonical_json(dict(self.codec.to_dict(state)))),
        )
        self.connection.commit()

    def load_checkpoint(self) -> T | None:
        if self.codec is None:
            raise StateFormatError("EventStore needs a codec to load state")
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (CHECKPOINT_KEY,)
        ).fetchone()
        return None if row is None else self.codec.from_dict(json.loads(row[0]))

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        """Discard events written since the last checkpoint, as a failed poll should."""
        self.connection.rollback()

    def close(self) -> None:
        self.connection.commit()
        self.connection.close()

    def __enter__(self) -> "EventStore[T]":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _require_metadata(self, key: str, value: str) -> None:
        row = self.connection.execute(
            "SELECT value FROM metadata WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            self.connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)", (key, value)
            )
        elif row[0] != value:
            raise StateFormatError(
                f"{self.path} belongs to {key}={row[0]!r}, not {value!r}"
            )


def _event(row: Iterable[Any]) -> Event:
    values = tuple(row)
    sequence, stamp, event_type, payload = values[0], values[1], values[2], values[3]
    classification = values[4] if len(values) > 4 else None
    return Event(
        sequence=None if sequence is None else int(sequence),
        observed_at=datetime.fromisoformat(str(stamp)),
        event_type=str(event_type),
        payload=json.loads(payload),
        classification=None if classification is None else str(classification),
    )


def _iso(observed_at: datetime | None) -> str:
    return (observed_at or datetime.now(timezone.utc)).isoformat()
