from abc import ABC, abstractmethod
from core.types import Bar
from typing import AsyncIterable

class DataSource(ABC):
    @abstractmethod
    async def __aiter__(self) -> AsyncIterable[Bar]:
        ...