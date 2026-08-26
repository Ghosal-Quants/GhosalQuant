from ..core.types import MarketEvent

from abc import ABC, abstractmethod
from typing import AsyncIterable


class DataSource(ABC):
    """
    An async stream of market events.

    A source may yield any mix of ``Bar``, ``OrderBook``, and ``PublicTrade``; the
    ``ExecutionSystem`` routes each to the matching strategy, broker, and portfolio hook. Sources
    that only know about bars simply yield bars.
    """

    @abstractmethod
    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        ...
