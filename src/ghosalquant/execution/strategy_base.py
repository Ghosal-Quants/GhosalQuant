from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterable, Union

from ..core.types import Bar, Fill, Order, OrderBook, PublicTrade, Signal
from .context import StrategyContext

SignalResult = Union[Signal, Iterable[Signal], None]


def as_signal_list(result: SignalResult) -> list[Signal]:
    """Normalise a hook's return value to a list of signals."""
    if result is None:
        return []
    if isinstance(result, Signal):
        return [result]
    signals = list(result)
    for signal in signals:
        if not isinstance(signal, Signal):
            raise TypeError(f"Strategy hooks must return Signal objects, got {type(signal).__name__}")
    return signals


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
    def on_market_data(self, bar: Bar) -> Signal | None: ...

    @abstractmethod
    def on_end(self) -> None: ...

    # ----- Order-book events (no-op by default) ----- #

    def on_order_book(self, book: OrderBook, context: StrategyContext) -> SignalResult:
        """
        Called for each ``OrderBook`` the data source yields for a symbol.

        ``context`` describes the strategy's own position and resting orders for that symbol at
        the moment of the book. Return ``None``, one ``Signal``, or an iterable of signals; each is
        passed through the ``SignalSink`` chain and the ``RiskManager`` in order.
        """
        return None

    def on_public_trade(self, trade: PublicTrade, context: StrategyContext) -> SignalResult:
        """Called for each ``PublicTrade`` (a print on the public tape). Same contract as ``on_order_book``."""
        return None

    # ----- Order lifecycle ----- #

    def on_fill(self, fill: Fill) -> None:
        """
        Called after the ``Portfolio`` has processed a fill. Stateful strategies use this to track
        their own inventory and average entry price.
        """
        return None

    def on_order_accepted(self, order: Order, broker_order_id: str | None) -> None:
        """
        Called after the ``Broker`` accepted an order, with the broker's id for it (``None`` when
        the broker does not return one). Lets a strategy associate later fills and
        ``OpenOrderView`` entries with the signal that produced them.
        """
        return None

    def on_order_expired(
        self, order: Order, broker_order_id: str, expired_at: datetime, context: StrategyContext
    ) -> SignalResult:
        """
        Called when a resting order passed its time to live, at the moment it expired rather than
        when the next market event arrived. Same signal contract as ``on_order_book``, so a
        strategy can requote there and then instead of waiting for its symbol to tick again.

        Timestamp a replacement with ``expired_at``, not with ``order.time_stamp``: the original is
        already older than its own time to live, so the replacement would expire immediately.
        """
        return None

    def calculate_quantity(self, confidence) -> float | None:
        if confidence >= self.confidence_threshold:
            return self.max_qty

        return self.min_qty


class OrderBookStrategy(Strategy):
    """
    A ``Strategy`` driven by order books and public trades.

    Bars still reach the loop (they mark the portfolio) but are ignored here; ``on_order_book`` is
    mandatory, ``on_public_trade`` optional.
    """

    def on_market_data(self, bar: Bar) -> Signal | None:
        return None

    @abstractmethod
    def on_order_book(self, book: OrderBook, context: StrategyContext) -> SignalResult: ...
