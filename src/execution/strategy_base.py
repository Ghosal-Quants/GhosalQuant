from abc import ABC, abstractmethod

from core.types import Bar
from core.types import Signal

class Strategy(ABC):
    @abstractmethod
    def on_start(self) -> None: ...

    @abstractmethod
    def on_end(self) -> None: ...

    @abstractmethod
    def on_market_data(self, bar: Bar) -> Signal: ...
