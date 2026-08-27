from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import uuid4
import json
import time

from .events import (
    BAR_KIND,
    ORDER_BOOK_KIND,
    PUBLIC_TRADE_KIND,
    RecordFormatError,
    bar_record,
    order_book_checksum,
    order_book_record,
    public_trade_record,
)
from .intervals import (
    INTERVAL_COUNT_COLUMN,
    INTERVAL_FIRST_COLUMN,
    INTERVAL_LAST_COLUMN,
    INTERVAL_SECONDS_COLUMN,
)
from .storage import (
    DEFAULT_ROTATE_BYTES,
    DEFAULT_ROTATE_SECONDS,
    SegmentedParquetWriter,
    iter_stream_records,
    read_checkpoint,
    write_checkpoint,
)
from ..core.types import Bar, MarketEvent, OrderBook, PublicTrade

DEFAULT_MAX_TRACKED_TRADE_IDS = 200_000


@dataclass(frozen=True, slots=True)
class RecordedBook:
    changed: bool
    checksum: str
    event_index: int | None = None


@dataclass(frozen=True, slots=True)
class RecordedTrades:
    new_trades: int
    total_trades: int


class StreamRecorder:
    """
    Base for recorders that append one durable stream and keep a resumable checkpoint.

    The stream is the write-ahead record; the checkpoint only caches what was derived from it, so
    a process that dies between the two is repaired on the next open by replaying the stream.
    """

    stream_name = "stream"
    checkpoint_version = 1

    def __init__(
        self,
        output_dir: str | Path,
        *,
        fsync: bool = False,
        checkpoint_interval_seconds: float = 1.0,
        recording_session_id: str | None = None,
        resume_scan: bool = True,
        rotate_bytes: int = DEFAULT_ROTATE_BYTES,
        rotate_seconds: float = DEFAULT_ROTATE_SECONDS,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.fsync = bool(fsync)
        self.checkpoint_interval_seconds = max(0.0, float(checkpoint_interval_seconds))
        self.recording_session_id = (
            str(recording_session_id) if recording_session_id is not None else uuid4().hex
        )
        self.stream_path = self.output_dir / f"{self.stream_name}.jsonl"
        self.state_path = self.output_dir / f"{self.stream_name}_state.json"
        self.summary_path = self.output_dir / f"{self.stream_name}_summary.json"

        self._record_count = 0
        self._records_by_symbol: dict[str, int] = {}
        self._closed = False
        self._last_checkpoint_monotonic = time.monotonic()

        self._restore(read_checkpoint(self.state_path))
        if resume_scan:
            self._scan_stream()

        self._writer = SegmentedParquetWriter(
            self.stream_path,
            fsync=self.fsync,
            rotate_bytes=rotate_bytes,
            rotate_seconds=rotate_seconds,
        )

    @property
    def record_count(self) -> int:
        return self._record_count

    @property
    def records_by_symbol(self) -> dict[str, int]:
        return dict(self._records_by_symbol)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.save_state(force=True)
        finally:
            # Whatever else fails, the stream's writer lock is released.
            self._writer.close()
        self.summary_path.write_text(
            json.dumps(self._summary_payload(), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    def save_state(self, *, force: bool = False) -> None:
        elapsed = time.monotonic() - self._last_checkpoint_monotonic
        if not force and elapsed < self.checkpoint_interval_seconds:
            return
        write_checkpoint(self.state_path, self._checkpoint_payload())
        self._last_checkpoint_monotonic = time.monotonic()

    def __enter__(self) -> "StreamRecorder":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _write(self, row: Mapping[str, Any], *, symbol: str) -> int:
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed")
        self._record_count += 1
        self._records_by_symbol[symbol] = self._records_by_symbol.get(symbol, 0) + 1
        self._writer.write(row)
        return self._record_count

    def _scan_stream(self) -> None:
        scanned: dict[str, int] = {}
        for row in iter_stream_records(self.stream_path):
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                scanned[symbol] = scanned.get(symbol, 0) + 1
            self._scan(row)
        for symbol, count in scanned.items():
            self._records_by_symbol[symbol] = max(self._records_by_symbol.get(symbol, 0), count)
        self._record_count = max(self._record_count, sum(scanned.values()))
        self._after_scan()

    def _restore(self, checkpoint: Mapping[str, Any]) -> None:
        self._record_count = int(checkpoint.get("record_count") or 0)
        self._records_by_symbol = {
            str(symbol): int(count)
            for symbol, count in (checkpoint.get("records_by_symbol") or {}).items()
        }

    def _scan(self, row: Mapping[str, Any]) -> None:
        ...

    def _after_scan(self) -> None:
        ...

    def _checkpoint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.checkpoint_version,
            "recording_session_id": self.recording_session_id,
            "record_count": self._record_count,
            "records_by_symbol": self._records_by_symbol,
        }

    def _summary_payload(self) -> dict[str, Any]:
        return {
            "stream": self.stream_name,
            "stream_path": str(self.stream_path),
            "record_count": self._record_count,
            "records_by_symbol": dict(sorted(self._records_by_symbol.items())),
        }


class OrderBookRecorder(StreamRecorder):
    """
    Record an order book only when the visible book changes.

    Writing every repeat of a polled book wastes space; writing only the changes loses the fact
    that it stood still. Both are kept: the repeats after a record extend its observation
    interval, which the next record carries forward.
    """

    stream_name = "order_book_events"
    checkpoint_version = 1

    def __init__(self, output_dir: str | Path, *, depth: int | None = None, **options: Any) -> None:
        self.depth = depth
        self._checksum_by_symbol: dict[str, str] = {}
        self._interval_by_symbol: dict[str, dict[str, Any]] = {}
        self._latest_by_symbol: dict[str, dict[str, Any]] = {}
        self._seen_in_session: set[str] = set()
        super().__init__(output_dir, **options)

    def record(self, book: OrderBook, *, observed_at: datetime | None = None) -> RecordedBook:
        seen_at = _as_utc(observed_at or book.time_stamp)
        checksum = order_book_checksum(book, depth=self.depth)
        prior = self._checksum_by_symbol.get(book.symbol)
        changed = prior != checksum
        if not changed and book.symbol in self._seen_in_session:
            self._extend_interval(book.symbol, seen_at)
            self.save_state()
            return RecordedBook(changed=False, checksum=checksum)

        previous_interval = self._interval_by_symbol.get(book.symbol)
        row = order_book_record(book, observed_at=seen_at, depth=self.depth)
        row["event_index"] = self._record_count + 1
        row["recording_session_id"] = self.recording_session_id
        row["snapshot_state_changed"] = changed
        row["snapshot_reason"] = (
            "first_seen" if prior is None else "changed" if changed else "session_start"
        )
        if previous_interval is not None:
            row["previous_snapshot_interval"] = dict(previous_interval)

        event_index = self._write(row, symbol=book.symbol)
        self._checksum_by_symbol[book.symbol] = checksum
        self._seen_in_session.add(book.symbol)
        self._interval_by_symbol[book.symbol] = _new_interval(
            book.symbol, event_index, checksum, self.recording_session_id, seen_at
        )
        self.save_state(force=True)
        return RecordedBook(changed=changed, checksum=checksum, event_index=event_index)

    def _extend_interval(self, symbol: str, observed_at: datetime) -> None:
        interval = self._interval_by_symbol.get(symbol)
        if interval is None:
            self._interval_by_symbol[symbol] = _new_interval(
                symbol,
                None,
                self._checksum_by_symbol.get(symbol),
                self.recording_session_id,
                observed_at,
            )
            return
        first = _parse_utc(interval.get(INTERVAL_FIRST_COLUMN))
        last = _parse_utc(interval.get(INTERVAL_LAST_COLUMN))
        if first is None or last is None or observed_at <= last:
            return
        count = max(1, int(interval.get(INTERVAL_COUNT_COLUMN) or 1)) + 1
        interval[INTERVAL_LAST_COLUMN] = _iso(observed_at)
        interval[INTERVAL_COUNT_COLUMN] = count
        interval[INTERVAL_SECONDS_COLUMN] = (observed_at - first).total_seconds() / (count - 1)

    def _restore(self, checkpoint: Mapping[str, Any]) -> None:
        super()._restore(checkpoint)
        self._checksum_by_symbol = {
            str(symbol): str(checksum)
            for symbol, checksum in (checkpoint.get("checksum_by_symbol") or {}).items()
        }
        self._interval_by_symbol = {
            str(symbol): dict(interval)
            for symbol, interval in (checkpoint.get("interval_by_symbol") or {}).items()
            if isinstance(interval, Mapping)
        }

    def _scan(self, row: Mapping[str, Any]) -> None:
        symbol = str(row.get("symbol") or "").strip()
        checksum = str(row.get("checksum") or "").strip()
        if symbol and checksum:
            self._checksum_by_symbol[symbol] = checksum
            self._latest_by_symbol[symbol] = dict(row)
        self._record_count = max(self._record_count, int(row.get("event_index") or 0))

    def _after_scan(self) -> None:
        # The stream outranks the checkpoint; a stale interval would extend a superseded state.
        for symbol, row in self._latest_by_symbol.items():
            checksum = str(row.get("checksum") or "")
            event_index = int(row.get("event_index") or 0)
            interval = self._interval_by_symbol.get(symbol)
            if (
                isinstance(interval, Mapping)
                and str(interval.get("checksum") or "") == checksum
                and int(interval.get("event_index") or 0) == event_index
            ):
                continue
            self._interval_by_symbol[symbol] = _new_interval(
                symbol,
                event_index or None,
                checksum,
                row.get("recording_session_id"),
                _parse_utc(row.get("observed_at")) or datetime.now(timezone.utc),
            )

    def _checkpoint_payload(self) -> dict[str, Any]:
        return super()._checkpoint_payload() | {
            "depth": self.depth,
            "checksum_by_symbol": self._checksum_by_symbol,
            "interval_by_symbol": self._interval_by_symbol,
        }

    def _summary_payload(self) -> dict[str, Any]:
        return super()._summary_payload() | {
            "depth": self.depth,
            "interval_by_symbol": self._interval_by_symbol,
        }


class PublicTradeRecorder(StreamRecorder):
    """
    Record public prints, dropping ones already seen.

    Feeds returning a rolling window repeat prints on every poll, so identified trades are
    deduplicated against a bounded window of recent ids — unbounded, it would grow with the
    recording and be rewritten on every checkpoint. A print with no id is always recorded.
    """

    stream_name = "public_trades"
    checkpoint_version = 1

    def __init__(
        self,
        output_dir: str | Path,
        *,
        max_tracked_trade_ids: int = DEFAULT_MAX_TRACKED_TRADE_IDS,
        **options: Any,
    ) -> None:
        if max_tracked_trade_ids <= 0:
            raise ValueError("max_tracked_trade_ids must be > 0")
        self.max_tracked_trade_ids = int(max_tracked_trade_ids)
        self._seen_ids: OrderedDict[str, None] = OrderedDict()
        super().__init__(output_dir, **options)

    def record(self, trade: PublicTrade, *, observed_at: datetime | None = None) -> bool:
        trade_id = None if trade.trade_id is None else str(trade.trade_id)
        if trade_id is not None and trade_id in self._seen_ids:
            return False
        self._write(public_trade_record(trade, observed_at=observed_at), symbol=trade.symbol)
        if trade_id is not None:
            self._remember(trade_id)
        self.save_state()
        return True

    def record_all(
        self, trades: Iterable[PublicTrade], *, observed_at: datetime | None = None
    ) -> RecordedTrades:
        new_trades = sum(
            1
            for trade in sorted(trades, key=lambda item: item.time_stamp)
            if self.record(trade, observed_at=observed_at)
        )
        if new_trades:
            self.save_state(force=True)
        return RecordedTrades(new_trades=new_trades, total_trades=self._record_count)

    def _remember(self, trade_id: str) -> None:
        self._seen_ids[trade_id] = None
        while len(self._seen_ids) > self.max_tracked_trade_ids:
            self._seen_ids.popitem(last=False)

    def _restore(self, checkpoint: Mapping[str, Any]) -> None:
        super()._restore(checkpoint)
        self._seen_ids = OrderedDict(
            (str(trade_id), None)
            for trade_id in (checkpoint.get("seen_trade_ids") or ())
            if trade_id is not None
        )

    def _scan(self, row: Mapping[str, Any]) -> None:
        trade_id = row.get("trade_id")
        if trade_id is not None:
            self._remember(str(trade_id))

    def _checkpoint_payload(self) -> dict[str, Any]:
        return super()._checkpoint_payload() | {
            "max_tracked_trade_ids": self.max_tracked_trade_ids,
            "seen_trade_ids": list(self._seen_ids),
        }


class BarRecorder(StreamRecorder):
    """Record bars as they arrive; a bar already summarises a period."""

    stream_name = "bars"
    checkpoint_version = 1

    def record(self, bar: Bar, *, observed_at: datetime | None = None) -> bool:
        self._write(bar_record(bar, observed_at=observed_at), symbol=bar.symbol)
        self.save_state()
        return True


class MarketEventRecorder:
    """
    Record a mixed market-event stream into one directory, one durable stream per kind.

    Streams open on first use, so a bar-only session leaves no empty book files behind.
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        depth: int | None = None,
        max_tracked_trade_ids: int = DEFAULT_MAX_TRACKED_TRADE_IDS,
        recording_session_id: str | None = None,
        **options: Any,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.depth = depth
        self.max_tracked_trade_ids = max_tracked_trade_ids
        self.recording_session_id = (
            str(recording_session_id) if recording_session_id is not None else uuid4().hex
        )
        self._options = options
        self._recorders: dict[str, StreamRecorder] = {}
        self._closed = False

    @property
    def recorders(self) -> dict[str, StreamRecorder]:
        return dict(self._recorders)

    @property
    def record_count(self) -> int:
        return sum(recorder.record_count for recorder in self._recorders.values())

    def record(self, event: MarketEvent, *, observed_at: datetime | None = None) -> bool:
        if self._closed:
            raise RuntimeError("market event recorder is closed")
        match event:
            case OrderBook():
                return self.order_books.record(event, observed_at=observed_at).changed
            case PublicTrade():
                return self.public_trades.record(event, observed_at=observed_at)
            case Bar():
                return self.bars.record(event, observed_at=observed_at)
            case _:
                raise RecordFormatError(f"Cannot record {type(event).__name__}")

    @property
    def order_books(self) -> OrderBookRecorder:
        return self._recorder(
            ORDER_BOOK_KIND, lambda shared: OrderBookRecorder(self.output_dir, depth=self.depth, **shared)
        )

    @property
    def public_trades(self) -> PublicTradeRecorder:
        return self._recorder(
            PUBLIC_TRADE_KIND,
            lambda shared: PublicTradeRecorder(
                self.output_dir, max_tracked_trade_ids=self.max_tracked_trade_ids, **shared
            ),
        )

    @property
    def bars(self) -> BarRecorder:
        return self._recorder(BAR_KIND, lambda shared: BarRecorder(self.output_dir, **shared))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # One stream failing to close must not strand the others' writer locks.
        failure: Exception | None = None
        for recorder in self._recorders.values():
            try:
                recorder.close()
            except Exception as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    def __enter__(self) -> "MarketEventRecorder":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _recorder(self, kind: str, build) -> Any:
        recorder = self._recorders.get(kind)
        if recorder is None:
            if self._closed:
                raise RuntimeError("market event recorder is closed")
            recorder = build(dict(self._options, recording_session_id=self.recording_session_id))
            self._recorders[kind] = recorder
        return recorder


def _new_interval(
    symbol: str,
    event_index: int | None,
    checksum: str | None,
    recording_session_id: Any,
    observed_at: datetime,
) -> dict[str, Any]:
    observed_iso = _iso(observed_at)
    return {
        "symbol": symbol,
        "event_index": event_index,
        "checksum": checksum,
        "recording_session_id": recording_session_id,
        INTERVAL_FIRST_COLUMN: observed_iso,
        INTERVAL_LAST_COLUMN: observed_iso,
        INTERVAL_COUNT_COLUMN: 1,
        INTERVAL_SECONDS_COLUMN: None,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat()


def _parse_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None
