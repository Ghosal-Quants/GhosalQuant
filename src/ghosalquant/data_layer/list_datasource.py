from typing import AsyncIterable, Sequence

from ..core.types import MarketEvent
from .datasource_base import DataSource

class ListDataSource(DataSource):
    """Yield an in-memory sequence of market events (bars, order books, public trades) in order."""

    def __init__(
        self,
        events: Sequence[MarketEvent] | None = None,
        *,
        bars: Sequence[MarketEvent] | None = None,
    ) -> None:
        if events is None and bars is None:
            raise ValueError("ListDataSource needs a sequence of events")
        if events is not None and bars is not None:
            raise ValueError("Pass either events or bars, not both")
        self._events = events if events is not None else bars

    @property
    def events(self) -> Sequence[MarketEvent]:
        return self._events

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        for event in self._events:
            yield event
