from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any


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

@dataclass(frozen=True, slots=True)
class Signal:
    time_stamp: datetime
    symbol: str
    signal_type: SignalType
    price: float
    confidence: float = 1.0
    quantity: float | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self):
        if not isinstance(self.signal_type, SignalType):
            raise TypeError("Signal type must be a SignalType")

        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Confidence must be between 0.0 and 1.0")

        if self.price is not None and not isinstance(self.price, (int, float)):
            raise TypeError("Price must be a number or None")

    def __str__(self) -> str:
        return (
            f"Signal Type: {self.signal_type.value}, "
            +f"Timestamp: {self.time_stamp}, "
            +f"Symbol: {self.symbol}, "
            +f"Confidence: {self.confidence}"
            +(", Price: " + str(self.price) if self.price is not None else "")
            +(", Metadata: " + str(self.metadata) if self.metadata is not None else "")
        )

    def __repr__(self) -> str:
        return str(self)

@dataclass(frozen=True, slots=True)
class Order:
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float
    price: float
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, (int, float)):
            raise TypeError("Order quantity must be numeric")

        if self.quantity <= 0:
            raise ValueError("Order quantity must be > 0")

        if not isinstance(self.price, (int, float)):
            raise TypeError("Order price must be numeric")

    def __str__(self) -> str:
        return (
                f"Side: {self.side.value}, "
                +f"Timestamp: {self.time_stamp}, "
                +f"Symbol: {self.symbol}, "
                +f"Quantity: {self.quantity}, "
                +f"Price: {self.price}"
                +(", Metadata: " + str(self.metadata) if self.metadata is not None else "")
        )


@dataclass
class Trade:
    id: int
    symbol: str
    signal_type: SignalType
    entry_price: float
    size: float
    open_time: datetime
    close_time: datetime | None = None
    realised_pnl: float | None = None

    def unrealised_pnl(self, current_price: float) -> float:
        # PnL is not applicable for trades where no operation was performed
        if self.signal_type is None:
            return 0.0

        # sign of pnl for BUY is always +1 (win), for SELL is always -1 (win)
        sign: int = 1 if self.signal_type is SignalType.BUY else -1
        return sign * (current_price - self.entry_price) * self.size

@dataclass(frozen=True, slots=True)
class BacktestKPMs:
    total_trades: int
    total_wins: int
    total_losses: int
    win_rate_pct: float
    total_pnl: float

    def as_dict(self) -> dict[str, float | int]:
        return asdict(self)

    def __str__(self) -> str:
        return (
            f"Total Trades: {self.total_trades}, "
            +f"Total Wins: {self.total_wins}, "
            +f"Total Losses: {self.total_losses}, "
            +f"Win Rate (%): {self.win_rate_pct:.2f}, "
            +f"Total pnl: {self.total_pnl:.2f}"
        )


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time_stamp: datetime
    equity: float
    realised_pnl: float
    unrealised_pnl: float
    num_open_trades: int
