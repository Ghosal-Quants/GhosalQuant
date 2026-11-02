from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4
import json
import os
import time

REPLACE_ATTEMPTS = 10
REPLACE_BACKOFF_SECONDS = 0.02


class StateFormatError(RuntimeError):
    ...


def retry_while_locked(action: Callable[[], Any]) -> None:
    """
    Retry a rename or delete that Windows is briefly refusing.

    A file that was only just written can still be held open for a moment, by a scanner or by the
    OS releasing the handle lazily, and the rename then fails with a sharing violation. On other
    platforms this loop never runs a second time.
    """
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            action()
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_BACKOFF_SECONDS * (attempt + 1))


def canonical_json(value: Any) -> str:
    """One encoding per value, so a hash of it is stable across processes and runs."""
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))


def write_json(path: str | Path, value: Mapping[str, Any], *, indent: int | None = 2) -> None:
    """
    Replace a JSON file in one step, so a crash leaves the previous contents intact.

    The new copy is written and fsynced under a temporary name before it takes the real one, and
    on POSIX the directory is fsynced too, so the rename itself survives a power loss.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    encoded = json.dumps(dict(value), indent=indent, sort_keys=True, default=str) + "\n"
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    retry_while_locked(lambda: os.replace(temporary, target))
    if os.name != "nt":
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def read_json(path: str | Path) -> dict[str, Any]:
    """The object a JSON file holds, or an empty one if it is not there yet."""
    source = Path(path)
    if not source.is_file():
        return {}
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateFormatError(f"Invalid JSON state {source}: {exc}") from exc
    if not isinstance(value, dict):
        raise StateFormatError(f"JSON state must be an object: {source}")
    return value
