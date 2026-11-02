from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from uuid import uuid4
import hashlib
import json
import os
import time

import pyarrow as pa
import pyarrow.parquet as pq

from ..state.atomic import StateFormatError, canonical_json, read_json, retry_while_locked, write_json

DEFAULT_ROTATE_BYTES = 32 * 1024 * 1024
DEFAULT_ROTATE_SECONDS = 24 * 60 * 60
DEFAULT_MIN_AGE_ROTATION_BYTES = 1024 * 1024
DEFAULT_BATCH_ROWS = 4096
SEGMENT_SCHEMA_VERSION = 1

STORAGE_METADATA_KEY = b"ghosalquant_storage"
STORAGE_METADATA_VALUE = b"segmented_recorder"
# Segments sealed before this package was upstreamed are still read.
LEGACY_STORAGE_METADATA_KEYS = (b"minecraft_hft_storage",)

# Checked in order; the first one present wins.
CLOCK_FIELDS = ("observed_at", "first_observed_at", "recorded_at", "started_at", "time_stamp")


class RecorderStorageError(RuntimeError):
    ...


@dataclass(frozen=True, slots=True)
class StreamRecord:
    source_path: Path
    source_line: int
    stream_line: int
    value: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CompactedSegment:
    source_path: Path
    parquet_path: Path
    rows: int
    source_bytes: int
    parquet_bytes: int
    records_sha256: str


class SegmentedParquetWriter:
    """
    Append JSON records to a write-ahead log and seal full logs as Parquet.

    The active log is a plain ``.jsonl`` file, so a crash loses at most what the OS had buffered.
    Past ``rotate_bytes``, or at ``rotate_seconds`` holding ``min_age_rotation_bytes``, it is
    sealed into an immutable zstd Parquet segment under a sibling ``.segments`` directory, checked
    by row count and content hash before the log is unlinked. A log left unsealed by a crash is
    sealed on the next open. One writer per stream, held by a lock file.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        fsync: bool = False,
        rotate_bytes: int = DEFAULT_ROTATE_BYTES,
        rotate_seconds: float = DEFAULT_ROTATE_SECONDS,
        min_age_rotation_bytes: int = DEFAULT_MIN_AGE_ROTATION_BYTES,
    ) -> None:
        self.path = Path(path)
        if self.path.suffix.lower() != ".jsonl":
            raise ValueError(f"Segmented recorder path must end in .jsonl: {self.path}")
        if rotate_bytes <= 0:
            raise ValueError("rotate_bytes must be positive")
        if rotate_seconds <= 0:
            raise ValueError("rotate_seconds must be positive")
        if min_age_rotation_bytes < 0:
            raise ValueError("min_age_rotation_bytes cannot be negative")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.segments_dir = segments_dir_for(self.path)
        self.fsync = bool(fsync)
        self.rotate_bytes = int(rotate_bytes)
        self.rotate_seconds = float(rotate_seconds)
        self.min_age_rotation_bytes = int(min_age_rotation_bytes)
        self._closed = False
        self._file = None
        self._lock_file = None
        self._opened_monotonic = time.monotonic()

        self._acquire_lock()
        try:
            if self.segments_dir.is_dir():
                self._recover_unsealed_logs()
            if self.path.is_file() and self.path.stat().st_size >= self.rotate_bytes:
                self._seal_legacy_path()
            self._active_path = self._next_active_path()
            self._open_active()
        except Exception:
            self._release_lock()
            raise

    @property
    def active_path(self) -> Path:
        return self._active_path

    def write(self, row: Mapping[str, Any]) -> None:
        if self._closed or self._file is None:
            raise RuntimeError(f"writer is closed: {self.path}")
        if not isinstance(row, Mapping):
            raise TypeError("recorder rows must be mappings")
        self._file.write(canonical_record_json(row) + "\n")
        self._file.flush()
        if self.fsync:
            os.fsync(self._file.fileno())
        if self._rotation_due():
            self.compact_now()

    def compact_now(self) -> CompactedSegment | None:
        """Seal the active log immediately, if it holds anything."""
        if self._closed or self._file is None:
            raise RuntimeError(f"writer is closed: {self.path}")
        self._file.flush()
        if self.fsync:
            os.fsync(self._file.fileno())
        if self._active_path.stat().st_size == 0:
            self._opened_monotonic = time.monotonic()
            return None

        self._file.close()
        raw_segment = self._active_path
        if raw_segment == self.path:
            self.segments_dir.mkdir(parents=True, exist_ok=True)
            raw_segment = self.segments_dir / f"segment-{_segment_id()}.jsonl"
            retry_while_locked(lambda: self.path.replace(raw_segment))
        try:
            return compact_jsonl_segment(raw_segment)
        finally:
            # A failed seal must still leave a writable log; the unsealed one waits for reopen.
            self._active_path = self._new_segment_log()
            self._open_active()

    def close(self) -> None:
        if self._closed:
            return
        if self._file is not None:
            self._file.flush()
            if self.fsync:
                os.fsync(self._file.fileno())
            self._file.close()
        self._release_lock()
        self._closed = True

    def __enter__(self) -> "SegmentedParquetWriter":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _rotation_due(self) -> bool:
        size = self._active_path.stat().st_size
        if size >= self.rotate_bytes:
            return True
        age = time.monotonic() - self._opened_monotonic
        return size >= self.min_age_rotation_bytes and age >= self.rotate_seconds

    def _next_active_path(self) -> Path:
        return self._new_segment_log() if self.segments_dir.is_dir() else self.path

    def _new_segment_log(self) -> Path:
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        return self.segments_dir / f"segment-{_segment_id()}.jsonl"

    def _open_active(self) -> None:
        self._active_path.parent.mkdir(parents=True, exist_ok=True)
        _truncate_torn_tail(self._active_path)
        self._file = self._active_path.open("a", encoding="utf-8", buffering=1)
        self._opened_monotonic = time.monotonic()

    def _recover_unsealed_logs(self) -> None:
        for raw_path in sorted(self.segments_dir.glob("segment-*.jsonl")):
            compact_jsonl_segment(raw_path)

    def _seal_legacy_path(self) -> None:
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        raw_segment = self.segments_dir / f"segment-{_segment_id()}.jsonl"
        retry_while_locked(lambda: self.path.replace(raw_segment))
        compact_jsonl_segment(raw_segment)

    def _acquire_lock(self) -> None:
        lock_path = self.path.with_suffix(".writer.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = lock_path.open("a+b")
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            _lock_exclusive(lock_file)
        except OSError as exc:
            lock_file.close()
            raise RecorderStorageError(
                f"Recorder stream already has an active writer: {self.path}"
            ) from exc
        self._lock_file = lock_file

    def _release_lock(self) -> None:
        if self._lock_file is None:
            return
        try:
            _unlock(self._lock_file)
        finally:
            self._lock_file.close()
            self._lock_file = None


def segments_dir_for(logical_path: str | Path) -> Path:
    return Path(logical_path).with_suffix(".segments")


def logical_path_for_segments(segments_dir: str | Path) -> Path:
    path = Path(segments_dir)
    if path.suffix != ".segments":
        raise ValueError(f"Not a recorder segments directory: {path}")
    return path.with_suffix(".jsonl")


def discover_logical_streams(root: str | Path) -> list[Path]:
    """Every recorded stream under ``root``, named by its logical ``.jsonl`` path."""
    base = Path(root)
    streams = {path for path in base.rglob("*.jsonl") if path.parent.suffix != ".segments"}
    streams.update(
        logical_path_for_segments(path) for path in base.rglob("*.segments") if path.is_dir()
    )
    return sorted(streams)


def stream_source_files(logical_path: str | Path) -> list[Path]:
    """The files backing a stream, sealed segments preferred over their unsealed logs."""
    logical = Path(logical_path)
    paths: list[Path] = []
    if logical.is_file():
        paths.append(logical)
    segments_dir = segments_dir_for(logical)
    if not segments_dir.is_dir():
        return paths

    by_stem: dict[str, dict[str, Path]] = {}
    for path in segments_dir.glob("segment-*"):
        if not path.is_file() or path.suffix not in {".jsonl", ".parquet"}:
            continue
        by_stem.setdefault(path.stem, {})[path.suffix] = path
    for stem in sorted(by_stem):
        variants = by_stem[stem]
        paths.append(variants.get(".parquet") or variants[".jsonl"])
    return paths


def iter_stream_records(logical_path: str | Path) -> Iterator[dict[str, Any]]:
    for record in iter_stream_records_with_provenance(logical_path):
        yield record.value


def iter_stream_records_with_provenance(logical_path: str | Path) -> Iterator[StreamRecord]:
    """
    Every record of a stream, across sealed segments and the live log, in order.

    Exact duplicates are dropped, so a log copied twice cannot double-count.
    """
    stream_line = 0
    seen: set[bytes] = set()
    for source_path in stream_source_files(logical_path):
        rows = (
            _iter_parquet_records(source_path)
            if source_path.suffix == ".parquet"
            else _iter_jsonl_records(source_path)
        )
        for source_line, value in rows:
            digest = hashlib.sha256(canonical_record_json(value).encode("utf-8")).digest()
            if digest in seen:
                continue
            seen.add(digest)
            stream_line += 1
            yield StreamRecord(source_path, source_line, stream_line, value)


def last_stream_record(logical_path: str | Path) -> dict[str, Any] | None:
    """The newest record of a stream, read from the tail rather than by scanning it."""
    candidates: list[tuple[datetime | None, int, dict[str, Any]]] = []
    for index, source_path in enumerate(stream_source_files(logical_path)):
        row = (
            _last_parquet_record(source_path)
            if source_path.suffix == ".parquet"
            else _last_jsonl_record(source_path)
        )
        if row is not None:
            candidates.append((record_time(row), index, row))
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (item[0] is not None, item[0] or datetime.min.replace(tzinfo=timezone.utc), item[1]),
    )[2]


def compact_jsonl_segment(path: str | Path) -> CompactedSegment:
    """Seal one write-ahead log into a verified Parquet segment and remove the log."""
    source = Path(path)
    if source.suffix.lower() != ".jsonl" or not source.is_file():
        raise RecorderStorageError(f"Missing JSONL segment: {source}")
    target = source.with_suffix(".parquet")
    profile = _profile_jsonl(source)

    if profile["rows"] == 0:
        retry_while_locked(source.unlink)
        return CompactedSegment(source, target, 0, profile["source_bytes"], 0, profile["records_sha256"])

    if target.is_file():
        _validate_parquet_segment(target, profile["rows"], profile["records_sha256"])
        retry_while_locked(source.unlink)
        return CompactedSegment(
            source, target, profile["rows"], profile["source_bytes"],
            target.stat().st_size, profile["records_sha256"],
        )

    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    schema = pa.schema(
        [
            pa.field("source_line", pa.int64(), nullable=False),
            pa.field("record_sha256", pa.string(), nullable=False),
            pa.field("record_json", pa.large_string(), nullable=False),
        ],
        metadata={
            STORAGE_METADATA_KEY: STORAGE_METADATA_VALUE,
            b"schema_version": str(SEGMENT_SCHEMA_VERSION).encode("ascii"),
            b"source_name": source.name.encode("utf-8"),
            b"source_bytes": str(profile["source_bytes"]).encode("ascii"),
            b"source_sha256": profile["source_sha256"].encode("ascii"),
            b"records_sha256": profile["records_sha256"].encode("ascii"),
            b"first_observed_at": profile["first_observed_at"].encode("utf-8"),
            b"last_observed_at": profile["last_observed_at"].encode("utf-8"),
        },
    )

    writer = pq.ParquetWriter(temporary, schema, compression="zstd", write_statistics=True)
    batch: list[dict[str, Any]] = []
    try:
        for source_line, value in _iter_jsonl_records(source):
            canonical = canonical_record_json(value)
            batch.append(
                {
                    "source_line": source_line,
                    "record_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "record_json": canonical,
                }
            )
            if len(batch) >= DEFAULT_BATCH_ROWS:
                writer.write_table(pa.Table.from_pylist(batch, schema=schema))
                batch.clear()
        if batch:
            writer.write_table(pa.Table.from_pylist(batch, schema=schema))
    finally:
        writer.close()

    _validate_parquet_segment(temporary, profile["rows"], profile["records_sha256"])
    retry_while_locked(lambda: os.replace(temporary, target))
    retry_while_locked(source.unlink)
    return CompactedSegment(
        source, target, profile["rows"], profile["source_bytes"],
        target.stat().st_size, profile["records_sha256"],
    )


def segment_metadata(path: str | Path) -> dict[str, str]:
    """
    The provenance a sealed segment carries, or nothing if it is not a recorder segment.

    Segments stamped before this package was upstreamed use a different key and are still read.
    """
    with pq.ParquetFile(Path(path)) as segment:
        metadata = segment.schema_arrow.metadata or {}
    keys = (STORAGE_METADATA_KEY, *LEGACY_STORAGE_METADATA_KEYS)
    if not any(metadata.get(key) == STORAGE_METADATA_VALUE for key in keys):
        return {}
    return {
        key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
        for key, value in metadata.items()
    }


def canonical_record_json(value: Mapping[str, Any]) -> str:
    """A record's one canonical encoding, so hashes and dedup are stable."""
    return canonical_json(dict(value))


def read_checkpoint(path: str | Path) -> dict[str, Any]:
    try:
        return read_json(path)
    except StateFormatError as exc:
        raise RecorderStorageError(f"Invalid recorder checkpoint: {exc}") from exc


def write_checkpoint(path: str | Path, value: Mapping[str, Any]) -> None:
    """Replace a checkpoint atomically, so a crash leaves the previous one intact."""
    write_json(path, value)


def record_time(value: Mapping[str, Any]) -> datetime | None:
    text = record_time_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def record_time_text(value: Mapping[str, Any]) -> str:
    for field in CLOCK_FIELDS:
        found = value.get(field)
        if found:
            return str(found)
    return ""


def _profile_jsonl(path: Path) -> dict[str, Any]:
    records_digest = hashlib.sha256()
    source_digest = hashlib.sha256()
    rows = 0
    first_observed_at = ""
    last_observed_at = ""
    for source_line, raw_line, is_last in _iter_jsonl_lines(path):
        source_digest.update(raw_line)
        value = _record_from_line(path, source_line, raw_line, is_last)
        if value is None:
            continue
        rows += 1
        records_digest.update(hashlib.sha256(canonical_record_json(value).encode("utf-8")).digest())
        stamp = record_time_text(value)
        if stamp:
            first_observed_at = first_observed_at or stamp
            last_observed_at = stamp
    return {
        "rows": rows,
        "source_bytes": path.stat().st_size,
        "source_sha256": source_digest.hexdigest(),
        "records_sha256": records_digest.hexdigest(),
        "first_observed_at": first_observed_at,
        "last_observed_at": last_observed_at,
    }


def _validate_parquet_segment(path: Path, expected_rows: int, expected_records_sha256: str) -> None:
    table = pq.read_table(path, columns=["record_json"])
    if table.num_rows != expected_rows:
        raise RecorderStorageError(
            f"Sealed segment {path} has {table.num_rows} rows, expected {expected_rows}"
        )
    digest = hashlib.sha256()
    for value in table.column("record_json").to_pylist():
        digest.update(hashlib.sha256(str(value).encode("utf-8")).digest())
    if digest.hexdigest() != expected_records_sha256:
        raise RecorderStorageError(f"Sealed segment {path} does not match its source records")


def _iter_jsonl_records(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    for source_line, raw_line, is_last in _iter_jsonl_lines(path):
        value = _record_from_line(path, source_line, raw_line, is_last)
        if value is not None:
            yield source_line, value


def _iter_jsonl_lines(path: Path) -> Iterator[tuple[int, bytes, bool]]:
    """Raw lines of a log, each flagged with whether it is the last one in the file."""
    with path.open("rb") as handle:
        pending: tuple[int, bytes] | None = None
        for source_line, raw_line in enumerate(handle, start=1):
            if pending is not None:
                yield pending[0], pending[1], False
            pending = (source_line, raw_line)
        if pending is not None:
            yield pending[0], pending[1], True


def _record_from_line(path: Path, source_line: int, raw_line: bytes, is_last: bool) -> dict[str, Any] | None:
    """
    One record, or ``None`` for a line that holds none.

    An unterminated final line was never finished — a killed process, or a writer still
    appending — so it was never durable and is dropped. Failing to parse anywhere else raises.
    """
    try:
        text = raw_line.decode("utf-8").strip()
        if not text:
            return None
        value = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        if is_last and not raw_line.endswith(b"\n"):
            return None
        raise RecorderStorageError(f"Malformed recorder record {path}:{source_line}: {exc}") from exc
    if not isinstance(value, dict):
        raise RecorderStorageError(f"Recorder rows must be objects: {path}:{source_line}")
    return value


def _truncate_torn_tail(path: Path) -> None:
    """
    Drop a half-written final line before appending after it.

    Otherwise the next record lands on the torn one and the two read as one unparseable line,
    turning a lost record into an unreadable stream.
    """
    if not path.is_file():
        return
    with path.open("rb+") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        if size == 0:
            return
        handle.seek(size - 1)
        if handle.read(1) == b"\n":
            return
        window = 0
        while window < size:
            window = min(size, max(window * 2, 8192))
            handle.seek(size - window)
            newline = handle.read(window).rfind(b"\n")
            if newline >= 0:
                handle.truncate(size - window + newline + 1)
                return
        handle.truncate(0)


def _iter_parquet_records(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    # Closed explicitly: an open segment holds a lock on Windows, and a reader still holding one
    # stops the writer sealing or replacing that file.
    with pq.ParquetFile(path) as parquet:
        for batch in parquet.iter_batches(columns=["source_line", "record_json"]):
            for row in batch.to_pylist():
                yield int(row["source_line"]), json.loads(row["record_json"])


def _last_jsonl_record(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        window = 0
        lines: list[bytes] = []
        while window < size:
            window = min(size, max(window * 2, 8192))
            handle.seek(size - window)
            chunk = handle.read(window)
            # The first line in a window may be partial, so two are needed to trust the last.
            complete = chunk if chunk.endswith(b"\n") else chunk.rpartition(b"\n")[0]
            lines = [line for line in complete.split(b"\n") if line.strip()]
            if len(lines) >= 2 or window == size:
                break
    for raw_line in reversed(lines):
        try:
            value = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _last_parquet_record(path: Path) -> dict[str, Any] | None:
    with pq.ParquetFile(path) as parquet:
        if parquet.metadata.num_rows == 0:
            return None
        group = parquet.read_row_group(parquet.num_row_groups - 1, columns=["record_json"])
    values = group.column("record_json").to_pylist()
    return json.loads(values[-1]) if values else None


def _lock_exclusive(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _segment_id() -> str:
    return f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S%f}-{uuid4().hex[:8]}"
