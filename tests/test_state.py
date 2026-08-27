import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from ghosalquant.state import (
    Event,
    EventLog,
    EventStore,
    StateCodec,
    StateFormatError,
    StateIdentityError,
    VersionedJsonStateStore,
    canonical_json,
    read_json,
    write_json,
)

T0 = datetime(2026, 5, 4, 9, 0, tzinfo=timezone.utc)


@dataclass
class Ledger:
    """Stand-in for whatever a live component needs to survive a restart."""

    cash: float = 0.0
    positions: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"cash": self.cash, "positions": dict(self.positions)}

    @staticmethod
    def from_dict(payload: dict) -> "Ledger":
        return Ledger(
            cash=float(payload.get("cash", 0.0)),
            positions={str(k): float(v) for k, v in (payload.get("positions") or {}).items()},
        )


CODEC = StateCodec(to_dict=Ledger.to_dict, from_dict=Ledger.from_dict)


# ----- Atomic JSON ----- #

def test_a_replaced_file_never_appears_half_written(tmp_path):
    path = tmp_path / "state.json"
    write_json(path, {"count": 1})
    write_json(path, {"count": 2})

    assert read_json(path) == {"count": 2}
    assert not list(tmp_path.glob(".state.json.*.tmp"))


def test_reading_what_is_not_there_yet_is_not_an_error(tmp_path):
    assert read_json(tmp_path / "missing.json") == {}


def test_a_corrupt_file_is_reported_rather_than_guessed_at(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(StateFormatError):
        read_json(path)


def test_canonical_json_does_not_depend_on_key_order():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


# ----- Versioned state ----- #

def test_state_survives_a_save_and_reload(tmp_path):
    store = VersionedJsonStateStore(tmp_path / "s.json", codec=CODEC)

    assert store.load() is None
    store.save(Ledger(cash=100.0, positions={"AAPL": 5.0}))

    assert store.load() == Ledger(cash=100.0, positions={"AAPL": 5.0})


def test_an_older_file_is_upgraded_rather_than_refused(tmp_path):
    path = tmp_path / "s.json"
    VersionedJsonStateStore(path, codec=CODEC, schema_version=1).save(Ledger(cash=50.0))

    def upgrade(payload: dict, from_version: int) -> dict:
        assert from_version == 1
        return dict(payload, positions={"MIGRATED": 1.0})

    newer = VersionedJsonStateStore(path, codec=CODEC, schema_version=2, upgrade=upgrade)

    assert newer.load() == Ledger(cash=50.0, positions={"MIGRATED": 1.0})


def test_a_file_from_a_newer_version_is_refused_not_half_read(tmp_path):
    path = tmp_path / "s.json"
    VersionedJsonStateStore(path, codec=CODEC, schema_version=3).save(Ledger(cash=50.0))

    with pytest.raises(StateFormatError):
        VersionedJsonStateStore(path, codec=CODEC, schema_version=1).load()


def test_state_from_a_different_configuration_is_refused(tmp_path):
    path = tmp_path / "s.json"
    VersionedJsonStateStore(path, codec=CODEC, identity="strategy-a").save(Ledger(cash=10.0))

    with pytest.raises(StateIdentityError):
        VersionedJsonStateStore(path, codec=CODEC, identity="strategy-b").load()

    assert VersionedJsonStateStore(path, codec=CODEC, identity="strategy-a").load().cash == 10.0


def test_a_failed_save_leaves_the_previous_state_intact(tmp_path, monkeypatch):
    path = tmp_path / "s.json"
    store = VersionedJsonStateStore(path, codec=CODEC)
    store.save(Ledger(cash=1.0))

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("ghosalquant.state.atomic.os.replace", explode)
    with pytest.raises(OSError):
        store.save(Ledger(cash=999.0))
    monkeypatch.undo()

    assert store.load() == Ledger(cash=1.0)


# ----- Append-only journal ----- #

def test_a_journal_keeps_what_happened_in_order(tmp_path):
    with EventLog(tmp_path / "journal.db") as log:
        log.append("signal", {"symbol": "AAPL"}, observed_at=T0)
        log.append("order", {"symbol": "AAPL", "quantity": 5}, observed_at=T0 + timedelta(seconds=1))

    with EventLog(tmp_path / "journal.db") as log:
        events = log.events()
        assert [event.event_type for event in events] == ["signal", "order"]
        assert events[1].payload == {"symbol": "AAPL", "quantity": 5}
        assert [event.event_type for event in log.events(event_type="order")] == ["order"]


# ----- Journal and checkpoint together ----- #

def test_the_checkpoint_commits_the_events_it_accounts_for(tmp_path):
    path = tmp_path / "store.db"
    with EventStore(path, codec=CODEC) as store:
        store.record("fill", {"symbol": "AAPL", "quantity": 5}, observed_at=T0)
        store.checkpoint(Ledger(cash=95.0, positions={"AAPL": 5.0}))

    with EventStore(path, codec=CODEC) as reopened:
        assert reopened.load_checkpoint() == Ledger(cash=95.0, positions={"AAPL": 5.0})
        assert [event.event_type for event in reopened.events()] == ["fill"]


def test_a_crash_before_the_checkpoint_loses_the_events_too(tmp_path):
    path = tmp_path / "store.db"
    store = EventStore(path, codec=CODEC)
    store.record("fill", {"symbol": "AAPL", "quantity": 5}, observed_at=T0)
    store.checkpoint(Ledger(cash=95.0))

    # A second poll that dies partway through: events written, no checkpoint.
    store.record("fill", {"symbol": "AAPL", "quantity": 7}, observed_at=T0 + timedelta(seconds=1))
    store.connection.close()

    with EventStore(path, codec=CODEC) as reopened:
        # State and journal still agree: neither the second fill nor a state that counted it.
        assert reopened.load_checkpoint() == Ledger(cash=95.0)
        assert len(reopened.events()) == 1


def test_a_failed_poll_can_roll_its_events_back(tmp_path):
    with EventStore(tmp_path / "store.db", codec=CODEC) as store:
        store.record("fill", {"quantity": 1}, observed_at=T0)
        store.checkpoint(Ledger(cash=1.0))

        store.record("fill", {"quantity": 2}, observed_at=T0 + timedelta(seconds=1))
        store.rollback()

        assert [event.payload for event in store.events()] == [{"quantity": 1}]


def test_recording_the_same_event_twice_stores_it_once(tmp_path):
    with EventStore(tmp_path / "store.db") as store:
        assert store.record("fill", {"id": "f1"}, observed_at=T0) is True
        assert store.record("fill", {"id": "f1"}, observed_at=T0) is False
        store.commit()

        assert len(store.events()) == 1
        assert store.event_counts() == {"fill": 1}


def test_events_can_be_counted_by_classification(tmp_path):
    with EventStore(tmp_path / "store.db") as store:
        store.record("fill", {"id": 1}, observed_at=T0, classification="live")
        store.record("fill", {"id": 2}, observed_at=T0, classification="shadow")
        store.record("order", {"id": 3}, observed_at=T0, classification="live")
        store.commit()

        assert store.event_counts() == {"fill": 2, "order": 1}
        assert store.event_counts(classification="live") == {"fill": 1, "order": 1}


def test_a_store_belonging_to_another_configuration_is_refused(tmp_path):
    path = tmp_path / "store.db"
    EventStore(path, identity={"strategy": "a"}).close()

    with pytest.raises(StateFormatError):
        EventStore(path, identity={"strategy": "b"})

    EventStore(path, identity={"strategy": "a"}).close()


def test_checkpointing_without_a_codec_is_an_error(tmp_path):
    with EventStore(tmp_path / "store.db") as store:
        with pytest.raises(StateFormatError):
            store.checkpoint(Ledger())


def test_the_journal_is_durable_by_configuration(tmp_path):
    path = tmp_path / "store.db"
    EventStore(path).close()

    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        columns = {row[1] for row in connection.execute("PRAGMA table_info(events)")}
    finally:
        connection.close()

    # The app called this column mark_sequence; upstream it is just the feed's sequence.
    assert "sequence" in columns and "mark_sequence" not in columns
