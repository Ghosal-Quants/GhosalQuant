from abc import ABC, abstractmethod

from core.types import Bar
from core.types import Signal

class Strategy(ABC):
    def __init__(
        self,
        confidence_threshold: float = 0.5,
        max_qty: int = 10,
        min_qty: int = 0,
    ):
        self.confidence_threshold = confidence_threshold
        self.max_qty = max_qty
        self.min_qty = min_qty

    @abstractmethod
    def on_start(self) -> None: ...

    @abstractmethod
    def on_market_data(self, bar: Bar) -> Signal: ...

    @abstractmethod
    def on_end(self) -> None: ...


    def calculate_quantity(self, confidence) -> float | None:
        if confidence >= self.confidence_threshold:
            return self.max_qty
        
        return self.min_qty
