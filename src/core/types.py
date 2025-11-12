from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from enum import Enum

@dataclass(frozen=True)
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

@dataclass(frozen=True)
class Signal:
    time_stamp: datetime
    symbol: str
    signal_type: SignalType
    confidence: float = 1.0
    price: float | None = None

    def __str__(self) -> str:
        return (
            "Signal Type: " + str(self.signal_type.value)
            +", Timestamp: " + str(self.time_stamp)
            +", Symbol: " + self.symbol
            +", Confidence: " + str(self.confidence)
            +(", Price: " + str(self.price) if self.price is not None else "")
        )

    def __repr__(self) -> str:
        return str(self)