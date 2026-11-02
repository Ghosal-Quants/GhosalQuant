from enum import Enum

from ..core.types import OrderBook, PublicTrade


class MarkPolicy(str, Enum):
    """
    How an order book or a public print becomes the price a position is valued at.

    ``MID``: the mid of a two-sided book; a print marks at its price.
    ``CONSERVATIVE``: the side that hurts — bid for longs, ask for shorts — and a print never
    improves a mark (never raises a long's mark, never lowers a short's).
    ``BARS``: neither moves a mark; bars and fills alone do, for a venue that publishes its own.

    Under every policy a one-sided or empty book keeps the previous mark: marking a long at the
    ask precisely when the bid has vanished is the wrong direction to be optimistic in.
    """
    MID = "mid"
    CONSERVATIVE = "conservative"
    BARS = "bars"


def mark_from_book(
    book: OrderBook,
    *,
    previous_mark: float | None,
    position_quantity: float,
    policy: MarkPolicy = MarkPolicy.MID,
) -> float | None:
    """The mark implied by ``book``, or ``previous_mark`` when the book cannot provide one."""
    if policy is MarkPolicy.BARS or not book.is_two_sided():
        return previous_mark
    if policy is MarkPolicy.MID:
        return book.mid_price
    if position_quantity > 0.0:
        return book.best_bid.price
    if position_quantity < 0.0:
        return book.best_ask.price
    return book.mid_price


def mark_from_trade(
    trade: PublicTrade,
    *,
    previous_mark: float | None,
    position_quantity: float,
    policy: MarkPolicy = MarkPolicy.MID,
) -> float | None:
    """The mark implied by ``trade``."""
    if policy is MarkPolicy.BARS:
        return previous_mark
    if policy is MarkPolicy.MID or previous_mark is None:
        return float(trade.price)
    if position_quantity > 0.0:
        return min(float(previous_mark), float(trade.price))
    if position_quantity < 0.0:
        return max(float(previous_mark), float(trade.price))
    return float(trade.price)
