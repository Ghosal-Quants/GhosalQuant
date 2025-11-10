from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class Bar:
    time_stamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class Signal: # TODO: expand
    time_stamp: datetime
    symbol: str