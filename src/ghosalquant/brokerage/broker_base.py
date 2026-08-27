from abc import ABC, abstractmethod
from datetime import datetime

from ..core.types import Bar, Fill, MarketEvent, Order, OrderBook, PublicTrade

class Broker(ABC):
    # ----- Managing Connection ----- #
    async def connect(self) -> None:
        """
        Optionally can be overridden by subclasses if connection management is needed on startup
        (no-op for simulation brokers)
        """
        return None

    async def disconnect(self) -> None:
        """
        Optionally can be overridden by subclasses if connection management is needed on shutdown
        (no-op for simulation brokers)
        """
        return None

    # ----- Market data ----- #
    def on_bar(self, bar: Bar) -> None:
        """
        Consume the latest market bar before fills are collected.

        Live brokers can ignore this hook. Simulation brokers can use it to
        advance market time and fill orders against observed liquidity.
        """
        return None

    def on_order_book(self, book: OrderBook) -> OrderBook:
        """
        Consume the latest order book before fills are collected.

        Returns the book the strategy should see. The default returns it unchanged; a simulation
        broker may return a copy adjusted for the strategy's own resting orders or consumed depth.
        The portfolio is always marked from the raw book, never from this return value.
        """
        return book

    def on_public_trade(self, trade: PublicTrade) -> None:
        """Consume a public print before fills are collected. Live brokers can ignore this hook."""
        return None

    def on_market_event(self, event: MarketEvent) -> MarketEvent:
        """Route a market event to the matching hook and return what the strategy should see."""
        match event:
            case Bar():
                self.on_bar(event)
                return event
            case OrderBook():
                return self.on_order_book(event)
            case PublicTrade():
                self.on_public_trade(event)
                return event
        raise TypeError(f"Unsupported market event: {type(event).__name__}")

    # ----- Order lifecycle ----- #
    def next_order_expiry_at(self) -> datetime | None:
        """When the earliest resting order falls due, or ``None`` if the broker expires none."""
        return None

    def expire_orders(self, *, now: datetime) -> list[str]:
        """
        Expire resting orders past their time to live, returning the ids removed.

        A live venue expires its own orders, so the default does nothing. A simulation broker
        overrides this, and the ``ExecutionSystem`` drives it from the clock so an order is gone
        the moment it falls due rather than when its symbol next ticks.
        """
        return []

    # ----- Execution ----- #
    @abstractmethod
    def submit(self, order: Order) -> str | None:
        """
        Submit an order using a broker-specific API

        Optionally returns a broker-specific order ID for tracking purposes
        """
        ...

    @abstractmethod
    def get_fills(self) -> list[Fill]:
        """
        Get fills since the last call

        Implementations should return a possibly empty list, and drain any internal fill queue
        """
        ...
