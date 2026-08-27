from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Mapping
import hashlib
import json
import math

INTERVAL_FIRST_COLUMN = "state_interval_first_observed_at"
INTERVAL_LAST_COLUMN = "state_interval_last_observed_at"
INTERVAL_COUNT_COLUMN = "state_interval_observation_count"
INTERVAL_SECONDS_COLUMN = "state_interval_effective_poll_seconds"
INTERVAL_OFFSETS_US_COLUMN = "state_interval_observation_offsets_us"

INTERVAL_COLUMNS = (
    INTERVAL_FIRST_COLUMN,
    INTERVAL_LAST_COLUMN,
    INTERVAL_COUNT_COLUMN,
    INTERVAL_SECONDS_COLUMN,
    INTERVAL_OFFSETS_US_COLUMN,
)

# Move on every sample, changed or not, so they are not part of state.
CLOCK_FIELDS = frozenset(
    {
        "as_of",
        "generated_at",
        "observed_at",
        "received_at",
        "requested_at",
        "server_time",
        "time_stamp",
        "timestamp",
        "updated_at",
    }
)

# Fields identifying the message a state arrived on, not the state itself. A reconstructed
# observation carries none: reusing the last number would break a consumer's gap check.
MESSAGE_IDENTITY_FIELDS = frozenset({"sequence"})

RECORDER_METADATA_FIELDS = frozenset(
    {
        "checksum",
        "event_index",
        "previous_snapshot_interval",
        "recording_session_id",
        "snapshot_reason",
        "snapshot_state_changed",
        "synthetic_observation",
        "synthetic_observation_index",
        *INTERVAL_COLUMNS,
    }
)


def record_state(row: Mapping[str, Any]) -> dict[str, Any]:
    """
    The part of a record that is evidence about the market, not about the recorder.

    Inclusion by default: anything not classified as a clock or as bookkeeping counts as state,
    so a newly recorded field creates state transitions rather than being compressed away.
    """
    return {
        str(key): value
        for key, value in row.items()
        if str(key) not in RECORDER_METADATA_FIELDS and str(key).lower() not in CLOCK_FIELDS
    }


def state_checksum(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def interval_observation_times(row: Mapping[str, Any]) -> tuple[datetime, ...]:
    """Recreate the times at which one unchanged state was observed."""
    return tuple(iter_interval_observation_times(row))


def iter_interval_observation_times(row: Mapping[str, Any]) -> Iterator[datetime]:
    """
    The same times, produced one at a time.

    One record can stand for tens of thousands of observations, so they are not materialised.
    """
    first = _utc_or_none(row.get(INTERVAL_FIRST_COLUMN) or row.get("observed_at"))
    last = _utc_or_none(row.get(INTERVAL_LAST_COLUMN) or row.get("observed_at"))
    count = _positive_int(row.get(INTERVAL_COUNT_COLUMN), default=1)
    if first is None or last is None or count <= 1 or last <= first:
        observed = _utc_or_none(row.get("observed_at")) or first
        if observed is not None:
            yield observed
        return

    offsets = _offsets(row.get(INTERVAL_OFFSETS_US_COLUMN))
    if len(offsets) == count and first + timedelta(microseconds=offsets[-1]) == last:
        for offset in offsets:
            yield first + timedelta(microseconds=offset)
        return

    # Without exact offsets, reconstruct the cadence from the recorded poll interval.
    span = (last - first).total_seconds()
    configured = _positive_float(row.get(INTERVAL_SECONDS_COLUMN))
    consistent = configured is not None and math.isclose(
        configured * (count - 1), span, rel_tol=0.01, abs_tol=0.001
    )
    step = configured if consistent else span / (count - 1)
    for index in range(count - 1):
        yield first + timedelta(seconds=step * index)
    yield last


def with_interval_observation_times(
    row: Mapping[str, Any], observation_times: Iterable[datetime]
) -> dict[str, Any]:
    """Attach an exact, non-empty observation interval to a record."""
    times = tuple(sorted({_as_utc(value) for value in observation_times}))
    if not times:
        raise ValueError("a state interval must contain at least one observation")
    first, last = times[0], times[-1]
    updated = dict(row)
    updated["observed_at"] = first
    updated[INTERVAL_FIRST_COLUMN] = first
    updated[INTERVAL_LAST_COLUMN] = last
    updated[INTERVAL_COUNT_COLUMN] = len(times)
    updated[INTERVAL_SECONDS_COLUMN] = (
        None if len(times) <= 1 else (last - first).total_seconds() / (len(times) - 1)
    )
    updated[INTERVAL_OFFSETS_US_COLUMN] = (
        [int(round((value - first).total_seconds() * 1_000_000)) for value in times]
        if len(times) > 1
        else []
    )
    return updated


def iter_rehydrated_records(rows: Iterable[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
    """
    Expand change-only records back into the full observation series they stand for.

    A record knows how long its state persisted, so it becomes one row per observation, with
    clocks advanced and the repeats flagged. Nothing is held between rows, so this works as a
    cursor into one record as well as a pass over a stream.
    """
    for row in rows:
        base = dict(row)
        original = _utc_or_none(base.get("observed_at"))
        for index, observed_at in enumerate(iter_interval_observation_times(base)):
            if original is None:
                original = observed_at
            hydrated = (
                dict(base)
                if observed_at == original
                else _shift_clocks(base, observed_at - original)
            )
            hydrated["observed_at"] = observed_at
            hydrated["synthetic_observation"] = index > 0
            hydrated["synthetic_observation_index"] = index
            if index > 0:
                hydrated["snapshot_reason"] = "rehydrated_unchanged_observation"
                if "snapshot_state_changed" in hydrated:
                    hydrated["snapshot_state_changed"] = False
                for field in MESSAGE_IDENTITY_FIELDS & hydrated.keys():
                    hydrated[field] = None
            yield hydrated


def _shift_clocks(row: Mapping[str, Any], delta: timedelta) -> dict[str, Any]:
    shifted = dict(row)
    for key, value in tuple(shifted.items()):
        if str(key).lower() not in CLOCK_FIELDS:
            continue
        parsed = _utc_or_none(value)
        if parsed is not None:
            shifted[key] = _like(value, parsed + delta)
    return shifted


def _like(original: Any, value: datetime) -> Any:
    """Render a shifted clock the way the original was written, offset and all."""
    if isinstance(original, datetime):
        return value if original.tzinfo is not None else value.replace(tzinfo=None)
    text = str(original)
    if text.endswith("Z"):
        return value.isoformat().replace("+00:00", "Z")
    try:
        naive = datetime.fromisoformat(text).tzinfo is None
    except ValueError:
        naive = False
    return value.replace(tzinfo=None).isoformat() if naive else value.isoformat()


def _utc_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed > 0.0 else None


def _offsets(value: Any) -> tuple[int, ...]:
    if value is None or isinstance(value, (str, bytes)):
        return ()
    try:
        offsets = tuple(int(item) for item in value)
    except (TypeError, ValueError):
        return ()
    if not offsets or offsets[0] != 0:
        return ()
    if any(right < left for left, right in zip(offsets, offsets[1:])):
        return ()
    return offsets
