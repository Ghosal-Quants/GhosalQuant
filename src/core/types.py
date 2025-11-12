from dataclasses import dataclass
from datetime import datetime
from typing import Literal

@dataclass(frozen=True)
class Bar:
    time_stamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

SignalType = Literal['BUY', 'SELL']

@dataclass(frozen=True)
class Signal:
    time_stamp: datetime
    symbol: str
    signal_type: SignalType
    confidence: float = 1.0
    price: float | None = None
