from abc import ABC, abstractmethod
from datetime import datetime, timedelta

from ..core import metadata_keys
from ..core.types import Bar, CashFlow, Fill, Liquidity, MarketEvent, Order, OrderBook, PublicTrade


def order_expires_at(order: Order, default_max_age_seconds: float | None = None) -> datetime | None:
    """When an order's time to live runs out: its own ``MAX_ORDER_AGE_SECONDS``, else the broker's default."""
    value = (order.metadata or {}).get(metadata_keys.MAX_ORDER_AGE_SECONDS)
    max_age = float(value) if value is not None and float(value) > 0.0 else default_max_age_seconds
    if max_age is None:
        return None
    return order.time_stamp + timedelta(seconds=max_age)


def order_liquidity(order: Order) -> Liquidity:
    """The liquidity an order declared: the field, else the older metadata key, else a resting maker."""
    if order.liquidity is not None:
        return order.liquidity
    value = (order.metadata or {}).get(metadata_keys.LIQUIDITY)
    return Liquidity(str(value).lower()) if value is not None else Liquidity.MAKER


def order_slot(order: Order) -> str | None:
    slot = (order.metadata or {}).get(metadata_keys.ORDER_SLOT)
    return None if slot is None else str(slot)


def cancels_remainder_after_first_fill(order: Order) -> bool:
    return bool((order.metadata or {}).get(metadata_keys.CANCEL_REMAINDER_AFTER_FIRST_FILL))


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
        Expire resting orders past their time to live, returning the ids expired.

        The default expires nothing, for a broker with no time to live. The ``ExecutionSystem``
        drives this from the clock, so an order goes the moment it falls due rather than when its
        symbol next ticks.
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

    def get_cash_flows(self) -> list[CashFlow]:
        """Cash flows booked since the last call, such as funding on an open future."""
        return []
