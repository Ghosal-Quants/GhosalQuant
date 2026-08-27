import asyncio
import json
import random
from datetime import datetime, timedelta, timezone
from itertools import islice
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core.types import Bar, BookLevel, OrderBook, PublicTrade, Signal, SignalType
from ghosalquant.data_layer import recording_datasource
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.data_layer.recording_datasource import RecordedDataSource, RecordingDataSource
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.recording import events, intervals, storage
from ghosalquant.recording.recorder import (
    BarRecorder,
    MarketEventRecorder,
    OrderBookRecorder,
    PublicTradeRecorder,
)
from ghosalquant.risk.risk_manager import RiskManager

T0 = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)


def make_book(seconds: float, price: float, *, symbol: str = "AAPL", quantity: float = 100.0) -> OrderBook:
    return OrderBook(
        time_stamp=T0 + timedelta(seconds=seconds),
        symbol=symbol,
        bids=(BookLevel(price, quantity), BookLevel(round(price - 0.01, 4), 250.0)),
        asks=(BookLevel(round(price + 0.02, 4), 150.0),),
        sequence=int(seconds),
    )


def make_trade(seconds: float, trade_id: str | None = None, *, symbol: str = "AAPL") -> PublicTrade:
    return PublicTrade(
        time_stamp=T0 + timedelta(seconds=seconds),
        symbol=symbol,
        price=100.01,
        quantity=5.0,
        trade_id=trade_id,
        aggressor=SignalType.BUY,
    )


def drain(source) -> list:
    async def collect():
        return [event async for event in source]

    return asyncio.run(collect())


# ----- Storage ----- #

def test_writer_seals_full_logs_into_verified_parquet(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path, rotate_bytes=120) as writer:
        for index in range(20):
            writer.write({"observed_at": (T0 + timedelta(seconds=index)).isoformat(), "index": index})

    sealed = sorted(storage.segments_dir_for(path).glob("*.parquet"))
    assert sealed, "a rotated log should be sealed as parquet"

    metadata = pq.ParquetFile(sealed[0]).schema_arrow.metadata
    assert metadata[storage.STORAGE_METADATA_KEY] == storage.STORAGE_METADATA_VALUE

    # The tail is still an open log; a reader spans both without being told which is which.
    assert list(storage.segments_dir_for(path).glob("segment-*.jsonl"))
    assert [row["index"] for row in storage.iter_stream_records(path)] == list(range(20))


def test_unsealed_log_is_sealed_on_reopen(tmp_path):
    path = tmp_path / "stream.jsonl"
    writer = storage.SegmentedParquetWriter(path, rotate_bytes=90)
    for index in range(8):
        writer.write({"index": index})
    leftover = writer.active_path
    writer._file.close()
    writer._release_lock()

    assert leftover.suffix == ".jsonl"
    storage.SegmentedParquetWriter(path).close()

    assert not leftover.exists()
    assert leftover.with_suffix(".parquet").is_file()
    assert [row["index"] for row in storage.iter_stream_records(path)] == list(range(8))


def test_sealed_segment_is_preferred_over_a_stale_copy_of_its_log(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path, rotate_bytes=60) as writer:
        for index in range(12):
            writer.write({"index": index})

    sealed = sorted(storage.segments_dir_for(path).glob("*.parquet"))[0]
    stale = sealed.with_suffix(".jsonl")
    stale.write_text(json.dumps({"index": 0}) + "\n", encoding="utf-8")

    assert sealed in storage.stream_source_files(path)
    assert stale not in storage.stream_source_files(path)


def test_duplicated_records_are_not_counted_twice(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path) as writer:
        writer.write({"index": 1})
        writer.write({"index": 2})
    path.write_text(path.read_text(encoding="utf-8") * 2, encoding="utf-8")

    assert [row["index"] for row in storage.iter_stream_records(path)] == [1, 2]


def test_a_tampered_segment_fails_verification(tmp_path):
    path = tmp_path / "stream.jsonl"
    segment = storage.segments_dir_for(path) / "segment-tampered.jsonl"
    segment.parent.mkdir(parents=True)
    segment.write_text("".join(json.dumps({"index": index}) + "\n" for index in range(4)), encoding="utf-8")
    storage.compact_jsonl_segment(segment)

    sealed = segment.with_suffix(".parquet")
    replacement = storage.segments_dir_for(path) / "segment-other.jsonl"
    replacement.write_text("".join(json.dumps({"index": index}) + "\n" for index in range(5)), encoding="utf-8")
    sealed.unlink()
    replacement.replace(segment)
    storage.compact_jsonl_segment(segment)

    # The parquet now holds five rows; a log claiming four must not be accepted against it.
    segment.write_text("".join(json.dumps({"index": index}) + "\n" for index in range(4)), encoding="utf-8")
    with pytest.raises(storage.RecorderStorageError):
        storage.compact_jsonl_segment(segment)


def test_a_half_written_final_record_is_dropped_not_fatal(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path) as writer:
        writer.write({"index": 1})
        writer.write({"index": 2})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"index": 3, "half')

    assert [row["index"] for row in storage.iter_stream_records(path)] == [1, 2]
    assert storage.last_stream_record(path)["index"] == 2

    # Appending after the torn bytes would fuse two records into one unreadable line.
    with storage.SegmentedParquetWriter(path) as writer:
        writer.write({"index": 4})
    assert [row["index"] for row in storage.iter_stream_records(path)] == [1, 2, 4]


def test_a_reader_tolerates_a_writer_mid_append(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path) as writer:
        writer.write({"index": 1})
        writer._file.write('{"index": 2, "still')
        writer._file.flush()

        assert [row["index"] for row in storage.iter_stream_records(path)] == [1]


def test_a_malformed_record_that_is_not_the_last_is_corruption(tmp_path):
    path = tmp_path / "stream.jsonl"
    path.write_text('{"index": 1}\n{"index": 2, "torn\n{"index": 3}\n', encoding="utf-8")

    with pytest.raises(storage.RecorderStorageError):
        list(storage.iter_stream_records(path))


def test_a_failed_seal_does_not_cost_the_caller_its_writer(tmp_path, monkeypatch):
    path = tmp_path / "stream.jsonl"
    writer = storage.SegmentedParquetWriter(path, rotate_bytes=30)

    def refuse(_path):
        raise storage.RecorderStorageError("seal refused")

    monkeypatch.setattr(storage, "compact_jsonl_segment", refuse)
    with pytest.raises(storage.RecorderStorageError):
        for index in range(10):
            writer.write({"index": index})

    monkeypatch.undo()
    writer.write({"index": "after"})
    writer.close()


def test_segments_sealed_before_the_rename_are_still_recognised(tmp_path):
    segment = storage.segments_dir_for(tmp_path / "stream.jsonl") / "segment-a.jsonl"
    segment.parent.mkdir(parents=True)
    segment.write_text('{"index": 1}\n', encoding="utf-8")
    sealed = storage.compact_jsonl_segment(segment).parquet_path

    assert storage.segment_metadata(sealed)["ghosalquant_storage"] == "segmented_recorder"

    table = pq.read_table(sealed)
    metadata = {
        key: value
        for key, value in table.schema.metadata.items()
        if key != storage.STORAGE_METADATA_KEY
    }
    metadata[storage.LEGACY_STORAGE_METADATA_KEYS[0]] = storage.STORAGE_METADATA_VALUE
    legacy = tmp_path / "legacy.parquet"
    pq.write_table(table.replace_schema_metadata(metadata), legacy)

    assert storage.segment_metadata(legacy)["records_sha256"]
    assert storage.segment_metadata(legacy)[
        storage.LEGACY_STORAGE_METADATA_KEYS[0].decode()
    ] == "segmented_recorder"


def test_unrelated_parquet_carries_no_segment_metadata(tmp_path):
    other = tmp_path / "other.parquet"
    pq.write_table(pa.table({"a": [1]}), other)

    assert storage.segment_metadata(other) == {}


def test_one_writer_per_stream(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path):
        with pytest.raises(storage.RecorderStorageError):
            storage.SegmentedParquetWriter(path)


def test_last_stream_record_reads_the_newest_record(tmp_path):
    path = tmp_path / "stream.jsonl"
    with storage.SegmentedParquetWriter(path, rotate_bytes=100) as writer:
        for index in range(12):
            writer.write({"observed_at": (T0 + timedelta(seconds=index)).isoformat(), "index": index})

    assert storage.last_stream_record(path)["index"] == 11
    assert storage.discover_logical_streams(tmp_path) == [path]


def test_checkpoints_survive_a_partial_write(tmp_path):
    path = tmp_path / "state.json"
    storage.write_checkpoint(path, {"count": 1})
    storage.write_checkpoint(path, {"count": 2})

    assert storage.read_checkpoint(path) == {"count": 2}
    assert not list(tmp_path.glob(".state.json.*.tmp"))

    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(storage.RecorderStorageError):
        storage.read_checkpoint(path)


# ----- Intervals ----- #

def test_observation_times_round_trip_exactly():
    times = [T0, T0 + timedelta(seconds=1.5), T0 + timedelta(seconds=4)]
    row = intervals.with_interval_observation_times({"symbol": "AAPL"}, times)

    assert intervals.interval_observation_times(row) == tuple(times)


def test_observation_times_reconstruct_a_constant_cadence():
    row = {
        "observed_at": T0.isoformat(),
        intervals.INTERVAL_FIRST_COLUMN: T0.isoformat(),
        intervals.INTERVAL_LAST_COLUMN: (T0 + timedelta(seconds=6)).isoformat(),
        intervals.INTERVAL_COUNT_COLUMN: 4,
        intervals.INTERVAL_SECONDS_COLUMN: 2.0,
    }

    assert intervals.interval_observation_times(row) == (
        T0,
        T0 + timedelta(seconds=2),
        T0 + timedelta(seconds=4),
        T0 + timedelta(seconds=6),
    )


def test_a_single_observation_needs_no_interval():
    assert intervals.interval_observation_times({"observed_at": T0.isoformat()}) == (T0,)
    assert intervals.interval_observation_times({}) == ()


def test_rehydration_advances_clocks_and_flags_repeats():
    row = intervals.with_interval_observation_times(
        {"symbol": "AAPL", "time_stamp": T0.isoformat(), "snapshot_state_changed": True},
        [T0, T0 + timedelta(seconds=3)],
    )
    hydrated = list(intervals.iter_rehydrated_records([row]))

    assert len(hydrated) == 2
    assert hydrated[0]["synthetic_observation"] is False
    assert hydrated[1]["synthetic_observation"] is True
    assert hydrated[1]["snapshot_state_changed"] is False
    assert hydrated[1]["time_stamp"] == (T0 + timedelta(seconds=3)).isoformat()


def test_record_state_keeps_evidence_and_drops_bookkeeping():
    row = {
        "symbol": "AAPL",
        "best_bid": 100.0,
        "observed_at": T0.isoformat(),
        "event_index": 7,
        "checksum": "abc",
        intervals.INTERVAL_COUNT_COLUMN: 3,
    }

    assert intervals.record_state(row) == {"symbol": "AAPL", "best_bid": 100.0}
    assert intervals.state_checksum({"a": 1}) == intervals.state_checksum({"a": 1})
    assert intervals.state_checksum({"a": 1}) != intervals.state_checksum({"a": 2})


# ----- Records ----- #

def test_market_events_round_trip_through_records():
    book = make_book(0, 100.0)
    trade = make_trade(1, "t1")
    bar = Bar(time_stamp=T0, symbol="AAPL", open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)

    for event in (book, trade, bar):
        record = json.loads(json.dumps(events.market_event_record(event)))
        rebuilt = events.market_event_from_record(record)
        # A recorded book carries the checksum the recorder stamped on it.
        assert rebuilt == (book.replace(checksum=rebuilt.checksum) if event is book else event)


def test_book_records_capped_to_depth_rebuild_that_depth():
    book = make_book(0, 100.0)
    record = events.order_book_record(book, depth=1)

    rebuilt = events.order_book_from_record(record)
    assert rebuilt.bids == (book.bids[0],)
    assert rebuilt.checksum == events.order_book_checksum(book, depth=1)
    assert events.order_book_checksum(book, depth=1) != events.order_book_checksum(book)


def test_checksums_ignore_time_but_not_size():
    assert events.order_book_checksum(make_book(0, 100.0)) == events.order_book_checksum(make_book(9, 100.0))
    assert events.order_book_checksum(make_book(0, 100.0)) != events.order_book_checksum(
        make_book(0, 100.0, quantity=101.0)
    )


# ----- Recorders ----- #

def test_only_changed_books_are_written(tmp_path):
    with OrderBookRecorder(tmp_path) as recorder:
        first = recorder.record(make_book(0, 100.0))
        repeat = recorder.record(make_book(1, 100.0))
        changed = recorder.record(make_book(2, 100.05))

    assert (first.changed, repeat.changed, changed.changed) == (True, False, True)
    assert first.event_index == 1 and repeat.event_index is None and changed.event_index == 2
    assert len(list(storage.iter_stream_records(tmp_path / "order_book_events.jsonl"))) == 2


def test_a_repeated_book_extends_the_interval_the_next_record_carries(tmp_path):
    with OrderBookRecorder(tmp_path) as recorder:
        recorder.record(make_book(0, 100.0))
        recorder.record(make_book(2, 100.0))
        recorder.record(make_book(4, 100.0))
        recorder.record(make_book(6, 100.05))

    rows = list(storage.iter_stream_records(tmp_path / "order_book_events.jsonl"))
    carried = rows[1]["previous_snapshot_interval"]

    assert carried["event_index"] == 1
    assert carried[intervals.INTERVAL_COUNT_COLUMN] == 3
    assert carried[intervals.INTERVAL_SECONDS_COLUMN] == 2.0


def test_a_recorder_resumes_where_a_killed_process_stopped(tmp_path):
    recorder = OrderBookRecorder(tmp_path)
    recorder.record(make_book(0, 100.0))
    recorder.record(make_book(2, 100.05))
    recorder._writer.close()  # a process killed without closing the recorder

    with OrderBookRecorder(tmp_path) as resumed:
        assert resumed.record_count == 2
        again = resumed.record(make_book(4, 100.05))
        assert (again.changed, again.event_index) == (False, 3)
        assert resumed.record(make_book(6, 100.10)).event_index == 4

    rows = list(storage.iter_stream_records(tmp_path / "order_book_events.jsonl"))
    assert [row["event_index"] for row in rows] == [1, 2, 3, 4]
    # Rewritten once on resume, so a recording opens with the state it resumed from.
    assert rows[2]["snapshot_reason"] == "session_start"


def test_a_stale_checkpoint_does_not_outrank_the_stream(tmp_path):
    recorder = OrderBookRecorder(tmp_path)
    recorder.record(make_book(0, 100.0))
    stale = storage.read_checkpoint(recorder.state_path)
    recorder.record(make_book(2, 100.05))
    recorder._writer.close()
    storage.write_checkpoint(recorder.state_path, stale)

    with OrderBookRecorder(tmp_path) as resumed:
        interval = resumed._interval_by_symbol["AAPL"]
        assert interval["event_index"] == 2


def test_repeated_prints_are_recorded_once(tmp_path):
    with PublicTradeRecorder(tmp_path) as recorder:
        result = recorder.record_all([make_trade(1, "t1"), make_trade(2, "t2")])
        repeat = recorder.record_all([make_trade(2, "t2"), make_trade(3, "t3")])

    assert (result.new_trades, repeat.new_trades) == (2, 1)
    assert repeat.total_trades == 3


def test_prints_without_an_id_are_always_recorded(tmp_path):
    with PublicTradeRecorder(tmp_path) as recorder:
        assert recorder.record(make_trade(1)) is True
        assert recorder.record(make_trade(1)) is True


def test_the_deduplication_window_is_bounded(tmp_path):
    with PublicTradeRecorder(tmp_path, max_tracked_trade_ids=2) as recorder:
        recorder.record_all([make_trade(index, f"t{index}") for index in range(4)])
        assert list(recorder._seen_ids) == ["t2", "t3"]

    checkpoint = storage.read_checkpoint(tmp_path / "public_trades_state.json")
    assert checkpoint["seen_trade_ids"] == ["t2", "t3"]


def test_the_market_event_recorder_opens_a_stream_per_kind_used(tmp_path):
    with MarketEventRecorder(tmp_path) as recorder:
        recorder.record(make_book(0, 100.0))
        recorder.record(make_trade(1, "t1"))

    assert (tmp_path / "order_book_events.jsonl").is_file()
    assert (tmp_path / "public_trades.jsonl").is_file()
    assert not (tmp_path / "bars.jsonl").exists()
    assert set(recorder.recorders) == {events.ORDER_BOOK_KIND, events.PUBLIC_TRADE_KIND}


def test_one_stream_failing_to_close_still_releases_the_others(tmp_path):
    recorder = MarketEventRecorder(tmp_path)
    recorder.record(make_book(0, 100.0))
    recorder.record(make_trade(1, "t1"))
    recorder.order_books.summary_path = tmp_path / "missing" / "deeper" / "summary.json"

    with pytest.raises(OSError):
        recorder.close()

    for stream in ("order_book_events", "public_trades"):
        storage.SegmentedParquetWriter(tmp_path / f"{stream}.jsonl").close()


def test_a_recording_replays_while_it_is_still_being_written(tmp_path):
    with MarketEventRecorder(tmp_path) as recorder:
        for index in range(6):
            recorder.record(make_book(index, 100.0 + index * 0.05))

        assert len(drain(RecordedDataSource(tmp_path))) == 6


def test_bars_are_recorded_as_they_arrive(tmp_path):
    bar = Bar(time_stamp=T0, symbol="AAPL", open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0)
    with BarRecorder(tmp_path) as recorder:
        recorder.record(bar)
        recorder.record(bar)

    assert recorder.record_count == 2


# ----- Recording and replaying a session ----- #

def session_events() -> list:
    stream = []
    for index in range(6):
        stream.append(make_book(index, 100.0 + (index // 2) * 0.05))
        stream.append(make_book(index, 50.0 + index * 0.01, symbol="MSFT"))
        if index % 2 == 0:
            stream.append(make_trade(index + 0.5, f"t{index}"))
    stream.append(Bar(time_stamp=T0 + timedelta(seconds=30), symbol="AAPL", open=1.0, high=2.0, low=0.5, close=1.5, volume=10.0))
    return stream


def test_recording_a_stream_does_not_change_it(tmp_path):
    stream = session_events()
    source = RecordingDataSource(ListDataSource(stream), tmp_path)

    assert drain(source) == stream


def test_a_recorded_session_replays_in_order(tmp_path):
    stream = session_events()
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    replayed = drain(RecordedDataSource(tmp_path))
    stamps = [event.time_stamp for event in replayed]

    assert stamps == sorted(stamps)
    assert [event for event in replayed if isinstance(event, PublicTrade)] == [
        event for event in stream if isinstance(event, PublicTrade)
    ]
    # By default a replay wakes a strategy as often as the live session did, repeats included.
    assert sorted(
        (event.symbol, event.time_stamp) for event in replayed if isinstance(event, OrderBook)
    ) == sorted(
        (event.symbol, event.time_stamp) for event in stream if isinstance(event, OrderBook)
    )


def test_a_replay_can_be_narrowed_to_the_changes(tmp_path):
    stream = session_events()
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    observed = sum(1 for event in stream if isinstance(event, OrderBook))
    changes = drain(RecordedDataSource(tmp_path, rehydrate_unchanged=False))
    replayed_books = [event for event in changes if isinstance(event, OrderBook)]

    # AAPL only moves every other tick, so three of the twelve observations were repeats.
    assert observed == 12
    assert len(replayed_books) == 9
    assert [event.time_stamp for event in changes] == sorted(event.time_stamp for event in changes)


def test_replay_can_be_narrowed_to_a_symbol_a_kind_and_a_window(tmp_path):
    drain(RecordingDataSource(ListDataSource(session_events()), tmp_path))

    books = drain(RecordedDataSource(tmp_path, kinds=[events.ORDER_BOOK_KIND], symbols=["MSFT"]))
    assert books and all(isinstance(event, OrderBook) and event.symbol == "MSFT" for event in books)

    window = drain(RecordedDataSource(tmp_path, start=T0 + timedelta(seconds=2), end=T0 + timedelta(seconds=4)))
    assert window and all(T0 + timedelta(seconds=2) <= event.time_stamp <= T0 + timedelta(seconds=4) for event in window)


def test_rehydrated_replay_restores_every_observation(tmp_path):
    stream = session_events()
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    rehydrated = drain(RecordedDataSource(tmp_path, rehydrate_unchanged=True))
    stamps = [event.time_stamp for event in rehydrated]
    recorded_books = [event for event in stream if isinstance(event, OrderBook)]
    replayed_books = [event for event in rehydrated if isinstance(event, OrderBook)]

    assert stamps == sorted(stamps)
    assert len(replayed_books) == len(recorded_books)
    assert [event.time_stamp for event in replayed_books] == sorted(
        event.time_stamp for event in recorded_books
    )


def test_naive_timestamps_replay_as_naive_timestamps(tmp_path):
    naive = datetime(2026, 3, 2, 14, 30)
    stream = [
        OrderBook(
            time_stamp=naive + timedelta(seconds=index),
            symbol="AAPL",
            bids=(BookLevel(100.0, 100.0),),
            asks=(BookLevel(100.02, 150.0),),
        )
        for index in range(4)
    ]
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    rehydrated = drain(RecordedDataSource(tmp_path, rehydrate_unchanged=True))

    assert [event.time_stamp for event in rehydrated] == [event.time_stamp for event in stream]
    assert all(event.time_stamp.tzinfo is None for event in rehydrated)


def test_rehydrating_reads_the_stream_a_fixed_number_of_times(tmp_path, monkeypatch):
    stream = [
        make_book(tick, 100.0 + index + (tick // 3) * 0.05, symbol=f"SYM{index:02d}")
        for tick in range(30)
        for index in range(20)
    ]
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    reads = []
    unpatched = recording_datasource.iter_stream_records

    def counted(path):
        reads.append(Path(path).name)
        return unpatched(path)

    monkeypatch.setattr(recording_datasource, "iter_stream_records", counted)
    replayed = drain(RecordedDataSource(tmp_path, rehydrate_unchanged=True))

    # One pass to index the intervals, one to replay — not one per symbol.
    assert reads.count("order_book_events.jsonl") == 2
    assert sorted((event.symbol, event.time_stamp) for event in replayed) == sorted(
        (event.symbol, event.time_stamp) for event in stream
    )


def test_a_quiet_symbol_does_not_stall_a_busy_one(tmp_path):
    stream = []
    for tick in range(200):
        stream.append(make_book(tick, 100.0 + tick * 0.01, symbol="BUSY"))
        stream.append(make_book(tick, 50.0, symbol="QUIET"))
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    replayed = drain(RecordedDataSource(tmp_path, rehydrate_unchanged=True))
    stamps = [event.time_stamp for event in replayed]

    # QUIET is one record standing for 200 observations, interleaved with every BUSY change.
    assert stamps == sorted(stamps)
    assert sum(1 for event in replayed if event.symbol == "QUIET") == 200
    assert sum(1 for event in replayed if event.symbol == "BUSY") == 200


def test_a_long_interval_is_walked_not_materialised():
    row = {
        "observed_at": T0.isoformat(),
        intervals.INTERVAL_FIRST_COLUMN: T0.isoformat(),
        intervals.INTERVAL_LAST_COLUMN: (T0 + timedelta(seconds=1_000_000)).isoformat(),
        intervals.INTERVAL_COUNT_COLUMN: 1_000_001,
        intervals.INTERVAL_SECONDS_COLUMN: 1.0,
    }
    walked = intervals.iter_interval_observation_times(row)

    assert list(islice(walked, 3)) == [T0, T0 + timedelta(seconds=1), T0 + timedelta(seconds=2)]


# ----- Compressed and uncompressed must behave the same ----- #

class _StalenessStrategy(OrderBookStrategy):
    """Quotes on the bid and pulls the quote once a book has stood still for four observations."""

    STALE = 4

    def __init__(self) -> None:
        super().__init__()
        self.seen: list = []
        self.signals: list = []
        self.pulls: list = []
        self.fills: list = []
        self._last: dict = {}
        self._stale: dict = {}

    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_order_book(self, book, context=None):
        self.seen.append(("book", book.symbol, book.time_stamp, book.bids, book.asks, book.sequence))
        level = (book.bids, book.asks)
        self._stale[book.symbol] = self._stale.get(book.symbol, 0) + 1 if self._last.get(book.symbol) == level else 0
        self._last[book.symbol] = level
        if self._stale[book.symbol] == self.STALE:
            self.pulls.append((book.symbol, book.time_stamp))
            return None
        if book.best_bid is None or (context is not None and context.has_open_orders):
            return None
        signal = Signal(
            time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
            ref_price=book.best_bid.price, confidence=1.0, quantity=5.0,
            limit_price=book.best_bid.price,
        )
        self.signals.append((signal.symbol, signal.time_stamp, signal.limit_price))
        return signal

    def on_public_trade(self, trade, context=None):
        self.seen.append(("trade", trade.symbol, trade.time_stamp, trade.price, trade.quantity, trade.trade_id))
        return None

    def on_fill(self, fill) -> None:
        self.fills.append(
            (fill.time_stamp, fill.symbol, fill.side.value, fill.quantity, fill.price,
             round(fill.commission, 12), fill.liquidity.value)
        )


def polled_stream(seed: int = 5, ticks: int = 200, symbols=("AAPL", "MSFT")) -> list:
    """A polled feed: every symbol reports every tick, but only sometimes moves."""
    rng = random.Random(seed)
    mids = {symbol: 100.0 + index for index, symbol in enumerate(symbols)}
    versions = {symbol: 0 for symbol in symbols}
    stream = []
    for tick in range(ticks):
        stamp = T0 + timedelta(seconds=tick)
        for symbol in symbols:
            if rng.random() < 0.25:
                mids[symbol] = round(mids[symbol] + rng.choice((-0.01, 0.01)), 2)
                versions[symbol] += 1
            mid = mids[symbol]
            stream.append(OrderBook(
                time_stamp=stamp, symbol=symbol,
                bids=(BookLevel(round(mid - 0.01, 2), 300.0), BookLevel(round(mid - 0.02, 2), 500.0)),
                asks=(BookLevel(round(mid + 0.01, 2), 300.0), BookLevel(round(mid + 0.02, 2), 500.0)),
                sequence=versions[symbol],
            ))
        if rng.random() < 0.25:
            symbol = rng.choice(symbols)
            stream.append(PublicTrade(
                time_stamp=stamp + timedelta(milliseconds=400), symbol=symbol,
                price=round(mids[symbol] - 0.01, 2), quantity=float(rng.randint(100, 600)),
                trade_id=f"p{tick}", aggressor=SignalType.SELL,
            ))
    return stream


def session_behaviour(source) -> dict:
    strategy = _StalenessStrategy()
    portfolio = BasicPortfolio(init_capital=100_000.0)
    broker = OrderBookSimBroker(maker_rebate_bps=0.2, taker_fee_bps=0.3, seed=3)
    asyncio.run(
        ExecutionSystem(
            source, strategy, portfolio=portfolio, risk_manager=RiskManager(), broker=broker
        ).run()
    )
    return {
        "seen": strategy.seen,
        "signals": strategy.signals,
        "pulls": strategy.pulls,
        "fills": strategy.fills,
        "realised_pnl": round(portfolio.realised_pnl, 12),
        "total_pnl": round(portfolio.total_pnl, 12),
        "equity": [(point.time_stamp, round(point.equity, 12)) for point in portfolio.equity_curve],
    }


def test_compressing_a_session_does_not_change_how_a_strategy_behaves(tmp_path):
    stream = polled_stream()
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    observed = sum(1 for event in stream if isinstance(event, OrderBook))
    stored = sum(1 for _ in storage.iter_stream_records(tmp_path / "order_book_events.jsonl"))
    assert stored < observed / 2, "the recording should actually be compressing something"

    live = session_behaviour(ListDataSource(stream))
    replayed = session_behaviour(RecordedDataSource(tmp_path))

    assert live["pulls"], "the strategy must depend on repeats for this to prove anything"
    # Everything a strategy acts on is reproduced; `sequence` is the one exception, pinned below.
    assert _without_sequence(live) == _without_sequence(replayed)


def _without_sequence(behaviour: dict) -> dict:
    return behaviour | {
        "seen": [row[:5] if row[0] == "book" else row for row in behaviour["seen"]]
    }


def test_a_reconstructed_observation_reports_no_sequence_number(tmp_path):
    stream = polled_stream()
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    stored = sum(1 for _ in storage.iter_stream_records(tmp_path / "order_book_events.jsonl"))
    replayed = [event for event in drain(RecordedDataSource(tmp_path)) if isinstance(event, OrderBook)]

    # A repeat the recorder never wrote has no number of its own, and reusing the last one would
    # break a consumer's gap check.
    assert sum(1 for event in replayed if event.sequence is not None) == stored
    assert sum(1 for event in replayed if event.sequence is None) == len(replayed) - stored

    changes = drain(RecordedDataSource(tmp_path, rehydrate_unchanged=False))
    assert all(event.sequence is not None for event in changes if isinstance(event, OrderBook))


def test_dropping_the_repeats_does_change_how_a_strategy_behaves(tmp_path):
    stream = polled_stream()
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    live = session_behaviour(ListDataSource(stream))
    changes_only = session_behaviour(RecordedDataSource(tmp_path, rehydrate_unchanged=False))

    # Why the repeats are replayed by default: without them a stale book never goes stale.
    assert changes_only["pulls"] == []
    assert len(changes_only["seen"]) < len(live["seen"])


def test_observations_sharing_a_timestamp_keep_a_stable_order(tmp_path):
    stream = []
    for tick in range(40):
        stream.append(make_book(tick, 100.0, symbol="AAPL"))              # never moves
        stream.append(make_book(tick, 50.0 + tick * 0.01, symbol="MSFT"))  # moves every tick
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    replayed = drain(RecordedDataSource(tmp_path))
    by_stamp: dict = {}
    for event in replayed:
        by_stamp.setdefault(event.time_stamp, []).append(event.symbol)

    # AAPL replays from one record and MSFT from forty, but the feed reported AAPL first.
    assert set(map(tuple, by_stamp.values())) == {("AAPL", "MSFT")}


def test_replaying_an_empty_recording_yields_nothing(tmp_path):
    assert drain(RecordedDataSource(tmp_path)) == []

    with pytest.raises(ValueError):
        RecordedDataSource(tmp_path, kinds=["quotes"])
