from typing import AsyncIterable, Sequence

from core.types import Bar
from data_layer.datasource_base import DataSource

class ListDataSource(DataSource):
    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = bars

    async def __aiter__(self) -> AsyncIterable[Bar]:
        for bar in self._bars:
            yield bar
