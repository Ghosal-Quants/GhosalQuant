from src.core.types import Bar
from typing import Protocol, AsyncIterable

class DataSource(Protocol):
    def __aiter__(self): AsyncIterable[Bar]: ...
