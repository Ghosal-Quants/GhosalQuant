from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any, AsyncIterable, Iterable, Iterator, Mapping
import heapq

from ..core.types import MarketEvent
from ..recording.events import BAR_KIND, ORDER_BOOK_KIND, PUBLIC_TRADE_KIND, market_event_from_record
from ..recording.intervals import (
    INTERVAL_COUNT_COLUMN,
    INTERVAL_FIRST_COLUMN,
    INTERVAL_LAST_COLUMN,
    INTERVAL_OFFSETS_US_COLUMN,
    INTERVAL_SECONDS_COLUMN,
    iter_rehydrated_records,
)
from ..recording.recorder import BarRecorder, MarketEventRecorder, OrderBookRecorder, PublicTradeRecorder
from ..recording.storage import iter_stream_records, read_checkpoint
from .datasource_base import DataSource
from .merge import merge_datasources

_STREAM_NAMES = {
    ORDER_BOOK_KIND: OrderBookRecorder.stream_name,
    PUBLIC_TRADE_KIND: PublicTradeRecorder.stream_name,
    BAR_KIND: BarRecorder.stream_name,
}


class RecordingDataSource(DataSource):
    """
    Record a live stream on its way through, without changing what the strategy sees.

    The same events are yielded in the same order. The recording is closed when the stream ends
    or the consumer stops iterating.
    """

    def __init__(self, source: DataSource, output_dir: str | Path, **recorder_options: Any) -> None:
        self._source = source
        self.recorder = MarketEventRecorder(output_dir, **recorder_options)

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        try:
            async for event in self._source:
                self.recorder.record(event)
                yield event
        finally:
            self.recorder.close()


class RecordedDataSource(DataSource):
    """
    Replay a recording as a time-ordered market event stream.

    The recorded streams are merged, narrowed by ``kinds``, ``symbols``, ``start`` and ``end``.

    Books are stored only when they change, but the repeats are replayed by default, so a strategy
    is woken as often as it was live — one reacting to a stale book needs those ticks to notice.
    Pass ``rehydrate_unchanged=False`` for the changes only.

    A replay drives a strategy the same way the live session did, except that a reconstructed
    observation reports ``sequence`` as ``None``: reusing the last number would break a gap check.
    """

    def __init__(
        self,
        recording_dir: str | Path,
        *,
        kinds: Iterable[str] | None = None,
        symbols: Iterable[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        rehydrate_unchanged: bool = True,
    ) -> None:
        self.recording_dir = Path(recording_dir)
        self.kinds = tuple(kinds) if kinds is not None else tuple(_STREAM_NAMES)
        self.symbols = frozenset(symbols) if symbols is not None else None
        self.start = start
        self.end = end
        self.rehydrate_unchanged = rehydrate_unchanged

        unknown = set(self.kinds) - set(_STREAM_NAMES)
        if unknown:
            raise ValueError(f"Unknown recorded kinds: {sorted(unknown)}")

    def stream_path(self, kind: str) -> Path:
        return self.recording_dir / f"{_STREAM_NAMES[kind]}.jsonl"

    def recorded_symbols(self, kind: str) -> tuple[str, ...]:
        """The symbols a recorded stream holds, from its checkpoint where one survived."""
        state = read_checkpoint(self.recording_dir / f"{_STREAM_NAMES[kind]}_state.json")
        recorded = set(state.get("records_by_symbol") or ())
        if not recorded:
            recorded = {
                str(row.get("symbol"))
                for row in iter_stream_records(self.stream_path(kind))
                if row.get("symbol")
            }
        if self.symbols is not None:
            recorded &= set(self.symbols)
        return tuple(sorted(recorded))

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        sources = [source for kind in self.kinds for source in self._sources_for(kind)]
        if not sources:
            return
        async for event in merge_datasources(*sources):
            yield event

    def _sources_for(self, kind: str) -> list[DataSource]:
        path = self.stream_path(kind)
        if not path.is_file() and not path.with_suffix(".segments").is_dir():
            return []
        rehydrate = kind == ORDER_BOOK_KIND and self.rehydrate_unchanged
        intervals = _interval_index(self.recording_dir, path) if rehydrate else None
        return [_RecordedStream(self, path, symbols=self.symbols, intervals=intervals)]


class _RecordedStream(DataSource):
    def __init__(
        self,
        owner: RecordedDataSource,
        path: Path,
        *,
        symbols: frozenset[str] | None,
        intervals: Mapping[int, Mapping[str, Any]] | None = None,
    ) -> None:
        self._owner = owner
        self._path = path
        self._symbols = symbols
        self._intervals = intervals

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        rows = self._plain_rows() if self._intervals is None else self._rehydrated_rows()
        for row in rows:
            event = market_event_from_record(row)
            if not _within(event.time_stamp, self._owner.start, self._owner.end):
                continue
            yield event

    def _plain_rows(self) -> Iterator[dict[str, Any]]:
        for row in iter_stream_records(self._path):
            if self._keeps(row):
                yield row

    def _rehydrated_rows(self) -> Iterator[dict[str, Any]]:
        """
        Expand every record's repeats in one pass, in time order.

        One symbol's repeats run past other symbols' records, so expansions have to interleave.
        Each record becomes a cursor holding one row at a time, ordered in a heap by observation
        time; reading a record at ``t`` releases everything before ``t``, since nothing later can
        be older than the record it belongs to. The heap therefore holds one row per symbol in
        flight, not the expanded timeline. Ties go to whichever symbol appeared first.
        """
        heap: list[tuple[datetime, int, int, dict[str, Any], Iterator[dict[str, Any]]]] = []
        ranks: dict[str, int] = {}
        sequence = count()

        def advance(cursor: Iterator[dict[str, Any]]) -> None:
            row = next(cursor, None)
            if row is not None:
                rank = ranks.setdefault(str(row.get("symbol")), len(ranks))
                heapq.heappush(
                    heap, (_as_utc(row["observed_at"]), rank, next(sequence), row, cursor)
                )

        def release(until: datetime | None) -> Iterator[dict[str, Any]]:
            while heap and (until is None or heap[0][0] < until):
                *_, row, cursor = heapq.heappop(heap)
                yield row
                advance(cursor)

        for record in iter_stream_records(self._path):
            if not self._keeps(record):
                continue
            yield from release(_record_time(record))
            interval = self._intervals.get(int(record.get("event_index") or 0))
            expanded = record if interval is None else dict(record) | dict(interval)
            advance(iter_rehydrated_records([expanded]))
        yield from release(None)

    def _keeps(self, row: Mapping[str, Any]) -> bool:
        return self._symbols is None or str(row.get("symbol")) in self._symbols


def _interval_index(recording_dir: Path, path: Path) -> dict[int, dict[str, Any]]:
    """
    How long each recorded book stood, keyed by the record it belongs to.

    A record carries the interval of the one before it, so an interval is only known once that
    symbol's next record arrives — hence this pass before replaying. Each symbol's last interval
    is still open and comes from the checkpoint. Only intervals that repeat are kept.
    """
    index: dict[int, dict[str, Any]] = {}

    def remember(interval: Any) -> None:
        if not isinstance(interval, Mapping):
            return
        event_index = int(interval.get("event_index") or 0)
        if event_index and int(interval.get(INTERVAL_COUNT_COLUMN) or 1) > 1:
            index.setdefault(event_index, _interval_columns(interval))

    for row in iter_stream_records(path):
        remember(row.get("previous_snapshot_interval"))

    state = read_checkpoint(recording_dir / f"{_STREAM_NAMES[ORDER_BOOK_KIND]}_state.json")
    for interval in (state.get("interval_by_symbol") or {}).values():
        remember(interval)
    return index


def _record_time(row: Mapping[str, Any]) -> datetime | None:
    value = row.get("observed_at") or row.get("time_stamp")
    if isinstance(value, datetime):
        return _as_utc(value)
    if value is None:
        return None
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _interval_columns(interval: Mapping[str, Any]) -> dict[str, Any]:
    return {
        column: interval.get(column)
        for column in (
            INTERVAL_FIRST_COLUMN,
            INTERVAL_LAST_COLUMN,
            INTERVAL_COUNT_COLUMN,
            INTERVAL_SECONDS_COLUMN,
            INTERVAL_OFFSETS_US_COLUMN,
        )
        if column in interval
    }


def _within(value: datetime, start: datetime | None, end: datetime | None) -> bool:
    if start is not None and _comparable(value, start) < _comparable(start, value):
        return False
    return end is None or _comparable(value, end) <= _comparable(end, value)


def _comparable(value: datetime, other: datetime) -> datetime:
    if (value.tzinfo is None) == (other.tzinfo is None):
        return value
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
