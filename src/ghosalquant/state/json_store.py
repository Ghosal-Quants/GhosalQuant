from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generic, Mapping, TypeVar

from .atomic import StateFormatError, read_json, write_json

T = TypeVar("T")

SCHEMA_VERSION_KEY = "schema_version"
IDENTITY_KEY = "identity"
UPDATED_AT_KEY = "updated_at"
STATE_KEY = "state"


class StateIdentityError(StateFormatError):
    """Raised when a state file was written under a configuration that is no longer the one running."""


@dataclass(frozen=True, slots=True)
class StateCodec(Generic[T]):
    """How one kind of state becomes JSON and comes back."""

    to_dict: Callable[[T], Mapping[str, Any]]
    from_dict: Callable[[Mapping[str, Any]], T]


class VersionedJsonStateStore(Generic[T]):
    """
    A component's state in one JSON file, written whole and readable across versions.

    - **Versioning.** An older file goes through ``upgrade`` before decoding, so a system can be
      restarted onto newer code without losing what it knew. A newer one is refused, not half-read.
    - **Identity.** When ``identity`` is set, state left by a different strategy or account is
      refused rather than silently resumed.
    - **Atomicity.** Saving replaces the file in one step, so a crash leaves the previous state.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        codec: StateCodec[T],
        schema_version: int = 1,
        identity: str | None = None,
        upgrade: Callable[[Mapping[str, Any], int], Mapping[str, Any]] | None = None,
    ) -> None:
        if schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        self.path = Path(path)
        self.codec = codec
        self.schema_version = int(schema_version)
        self.identity = identity
        self.upgrade = upgrade

    def load(self) -> T | None:
        """The stored state, or ``None`` when nothing has been saved yet."""
        payload = read_json(self.path)
        if not payload:
            return None

        version = int(payload.get(SCHEMA_VERSION_KEY, 0))
        if not 1 <= version <= self.schema_version:
            raise StateFormatError(
                f"{self.path} was written by schema version {version}, "
                f"and this reader is version {self.schema_version}"
            )
        stored_identity = payload.get(IDENTITY_KEY)
        if self.identity is not None and stored_identity != self.identity:
            raise StateIdentityError(
                f"{self.path} belongs to {stored_identity!r}, not {self.identity!r}"
            )

        state = payload.get(STATE_KEY, {})
        if version < self.schema_version and self.upgrade is not None:
            state = self.upgrade(state, version)
        return self.codec.from_dict(state)

    def save(self, state: T) -> None:
        write_json(self.path, {
            SCHEMA_VERSION_KEY: self.schema_version,
            IDENTITY_KEY: self.identity,
            UPDATED_AT_KEY: datetime.now(timezone.utc).isoformat(),
            STATE_KEY: dict(self.codec.to_dict(state)),
        })
