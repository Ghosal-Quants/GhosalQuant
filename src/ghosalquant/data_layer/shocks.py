from dataclasses import dataclass, replace
from datetime import datetime
from typing import AsyncIterable

from ..core.types import Bar, BookLevel, MarketEvent, OrderBook, PublicTrade
from .datasource_base import DataSource


@dataclass(frozen=True, slots=True)
class Shock:
    """
    A market that did not happen.

    ``price_fraction`` moves prices proportionally (-0.05 gaps the market down 5%),
    ``price_offset`` moves them by an amount, and ``depth_fraction`` scales size. Dated with
    ``at``, the shock starts there and holds, which is a gap partway through a session; undated,
    the whole stream is shocked, which is a different market.
    """

    price_fraction: float = 0.0
    price_offset: float = 0.0
    depth_fraction: float = 1.0
    at: datetime | None = None
    symbols: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if self.depth_fraction < 0.0:
            raise ValueError("depth_fraction must be >= 0")

    def applies(self, event: MarketEvent) -> bool:
        if self.symbols is not None and event.symbol not in self.symbols:
            return False
        return self.at is None or event.time_stamp >= self.at

    def price(self, price: float) -> float:
        moved = price * (1.0 + self.price_fraction) + self.price_offset
        if moved <= 0.0:
            raise ValueError(f"shock moves {price} to {moved}, which is not a market")
        return moved

    def size(self, size: float) -> float:
        return size * self.depth_fraction


def shocked(event: MarketEvent, *shocks: Shock) -> MarketEvent:
    """The event as it would have been under these shocks, applied in order."""
    for shock in shocks:
        if shock.applies(event):
            event = _apply(event, shock)
    return event


class ShockedDataSource(DataSource):
    """
    Replay a stream through a market that did not happen.

    Wraps any source, so the same recording runs twice — once as it was, once gapped — and the two
    are compared. Everything downstream is unchanged: the strategy, broker and risk rules see the
    shocked market as the real one.
    """

    def __init__(self, source: DataSource, *shocks: Shock) -> None:
        if not shocks:
            raise ValueError("ShockedDataSource needs at least one shock")
        self._source = source
        self._shocks = shocks

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        async for event in self._source:
            yield shocked(event, *self._shocks)


def _apply(event: MarketEvent, shock: Shock) -> MarketEvent:
    match event:
        case OrderBook():
            return event.replace(
                bids=tuple(_level(level, shock) for level in event.bids),
                asks=tuple(_level(level, shock) for level in event.asks),
                # The checksum described the book that actually traded.
                checksum=None,
            )
        case PublicTrade():
            return replace(
                event, price=shock.price(event.price), quantity=shock.size(event.quantity)
            )
        case Bar():
            return replace(
                event,
                open=shock.price(event.open),
                high=shock.price(event.high),
                low=shock.price(event.low),
                close=shock.price(event.close),
                volume=shock.size(event.volume),
            )
    raise TypeError(f"Cannot shock {type(event).__name__}")


def _level(level: BookLevel, shock: Shock) -> BookLevel:
    return BookLevel(price=shock.price(level.price), quantity=shock.size(level.quantity))
