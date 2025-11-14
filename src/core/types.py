from dataclasses import dataclass
from datetime import datetime
from enum import Enum

@dataclass(frozen=True, slots=True)
class Bar:
    time_stamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

class SignalType(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'
    NOOP = 'NOOP' # No Operation (Hold) - Optional instead of `None`

@dataclass(frozen=True, slots=True)
class Signal:
    time_stamp: datetime
    symbol: str
    signal_type: SignalType
    confidence: float = 1.0
    price: float | None = None

    def __str__(self) -> str:
        return (
            f"Signal Type: {self.signal_type.value}, "
            +f"Timestamp: {self.time_stamp}, "
            +f"Symbol: {self.symbol}, "
            +f"Confidence: {self.confidence}"
            +(", Price: " + str(self.price) if self.price is not None else "")
        )

    def __repr__(self) -> str:
        return str(self)