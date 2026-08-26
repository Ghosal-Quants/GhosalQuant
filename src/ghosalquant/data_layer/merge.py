import heapq
from typing import AsyncIterable

from ..core.types import Bar, MarketEvent, OrderBook, PublicTrade
from .datasource_base import DataSource

_PRIORITY: dict[type, int] = {PublicTrade: 0, OrderBook: 1, Bar: 2}

_END = object()


class MergedDataSource(DataSource):
    """
    Merge several data sources into one time-ordered stream.

    Each source must yield events in non-decreasing ``time_stamp`` order; the merge is then a
    k-way merge with a stable tiebreak (public trade, order book, bar; then source order). Events
    from one source keep their relative order regardless of type.
    """

    def __init__(self, *sources: DataSource) -> None:
        if not sources:
            raise ValueError("merge_datasources needs at least one source")
        self._sources = tuple(sources)

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        iterators = [aiter(source) for source in self._sources]
        heap: list[tuple] = []
        sequence = 0

        async def push(index: int) -> None:
            nonlocal sequence
            event = await anext(iterators[index], _END)
            if event is _END:
                return
            sequence += 1
            heapq.heappush(heap, (event.time_stamp, _PRIORITY.get(type(event), 3), index, sequence, event))

        for index in range(len(iterators)):
            await push(index)

        while heap:
            _, _, index, _, event = heapq.heappop(heap)
            yield event
            await push(index)


def merge_datasources(*sources: DataSource) -> DataSource:
    """Combine data sources (for example a book feed and a trade feed) into one ``DataSource``."""
    if len(sources) == 1:
        return sources[0]
    return MergedDataSource(*sources)
