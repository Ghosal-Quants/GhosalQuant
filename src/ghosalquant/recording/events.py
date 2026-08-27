from datetime import datetime, timezone
from typing import Any, Mapping
import hashlib
import json

from ..core.types import Bar, BookLevel, MarketEvent, OrderBook, PublicTrade, SignalType

BAR_KIND = "bar"
ORDER_BOOK_KIND = "order_book"
PUBLIC_TRADE_KIND = "public_trade"

RECORD_KINDS = (ORDER_BOOK_KIND, PUBLIC_TRADE_KIND, BAR_KIND)


class RecordFormatError(ValueError):
    ...


def record_kind(event: MarketEvent) -> str:
    match event:
        case OrderBook():
            return ORDER_BOOK_KIND
        case PublicTrade():
            return PUBLIC_TRADE_KIND
        case Bar():
            return BAR_KIND
        case _:
            raise RecordFormatError(f"Cannot record {type(event).__name__}")


def market_event_record(event: MarketEvent, *, observed_at: datetime | None = None, depth: int | None = None) -> dict[str, Any]:
    match event:
        case OrderBook():
            return order_book_record(event, observed_at=observed_at, depth=depth)
        case PublicTrade():
            return public_trade_record(event, observed_at=observed_at)
        case Bar():
            return bar_record(event, observed_at=observed_at)
        case _:
            raise RecordFormatError(f"Cannot record {type(event).__name__}")


def market_event_from_record(row: Mapping[str, Any]) -> MarketEvent:
    kind = str(row.get("kind") or "")
    builder = _BUILDERS.get(kind)
    if builder is None:
        raise RecordFormatError(f"Unknown record kind: {kind!r}")
    return builder(row)


# ----- Order books ----- #

def order_book_record(book: OrderBook, *, observed_at: datetime | None = None, depth: int | None = None) -> dict[str, Any]:
    """
    One order book as a self-describing record.

    Levels are stored in full (to ``depth`` when capped) so the book rebuilds exactly. The derived
    quantities are stored too, to save a reader rebuilding every book.
    """
    bids = _levels(book.bids, depth)
    asks = _levels(book.asks, depth)
    return {
        "kind": ORDER_BOOK_KIND,
        "observed_at": _iso(observed_at or book.time_stamp),
        "time_stamp": _iso(book.time_stamp),
        "symbol": book.symbol,
        "sequence": book.sequence,
        "depth": depth,
        "checksum": order_book_checksum(book, depth=depth),
        "best_bid": None if book.best_bid is None else book.best_bid.price,
        "best_ask": None if book.best_ask is None else book.best_ask.price,
        "spread": book.spread,
        "mid_price": book.mid_price,
        "bid_quantity": sum(level["quantity"] for level in bids),
        "ask_quantity": sum(level["quantity"] for level in asks),
        "bids": bids,
        "asks": asks,
    }


def order_book_from_record(row: Mapping[str, Any]) -> OrderBook:
    return OrderBook(
        time_stamp=_time(row, "time_stamp", "observed_at"),
        symbol=str(row["symbol"]),
        bids=tuple(_level(level) for level in row.get("bids") or ()),
        asks=tuple(_level(level) for level in row.get("asks") or ()),
        sequence=None if row.get("sequence") is None else int(row["sequence"]),
        checksum=row.get("checksum") or None,
    )


def order_book_checksum(book: OrderBook, *, depth: int | None = None) -> str:
    """A content hash of the visible book, so an unchanged book is recognised without comparing it."""
    payload = {
        "symbol": book.symbol,
        "bids": _levels(book.bids, depth),
        "asks": _levels(book.asks, depth),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# ----- Public trades ----- #

def public_trade_record(trade: PublicTrade, *, observed_at: datetime | None = None) -> dict[str, Any]:
    seen_at = observed_at or trade.observed_at or trade.time_stamp
    return {
        "kind": PUBLIC_TRADE_KIND,
        "observed_at": _iso(seen_at),
        "time_stamp": _iso(trade.time_stamp),
        "symbol": trade.symbol,
        "price": float(trade.price),
        "quantity": float(trade.quantity),
        "trade_id": trade.trade_id,
        "aggressor": None if trade.aggressor is None else trade.aggressor.value,
    }


def public_trade_from_record(row: Mapping[str, Any]) -> PublicTrade:
    time_stamp = _time(row, "time_stamp", "observed_at")
    observed_at = _time_or_none(row.get("observed_at"))
    aggressor = row.get("aggressor")
    return PublicTrade(
        time_stamp=time_stamp,
        symbol=str(row["symbol"]),
        price=float(row["price"]),
        quantity=float(row["quantity"]),
        trade_id=None if row.get("trade_id") is None else str(row["trade_id"]),
        aggressor=None if aggressor is None else SignalType(str(aggressor).upper()),
        observed_at=None if observed_at is None or observed_at == time_stamp else observed_at,
    )


# ----- Bars ----- #

def bar_record(bar: Bar, *, observed_at: datetime | None = None) -> dict[str, Any]:
    return {
        "kind": BAR_KIND,
        "observed_at": _iso(observed_at or bar.time_stamp),
        "time_stamp": _iso(bar.time_stamp),
        "symbol": bar.symbol,
        "open": float(bar.open),
        "high": float(bar.high),
        "low": float(bar.low),
        "close": float(bar.close),
        "volume": float(bar.volume),
    }


def bar_from_record(row: Mapping[str, Any]) -> Bar:
    return Bar(
        time_stamp=_time(row, "time_stamp", "observed_at"),
        symbol=str(row["symbol"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
    )


def _levels(levels: tuple[BookLevel, ...], depth: int | None) -> list[dict[str, float]]:
    selected = levels if depth is None else levels[:depth]
    return [{"price": float(level.price), "quantity": float(level.quantity)} for level in selected]


def _level(value: Any) -> BookLevel:
    if isinstance(value, Mapping):
        return BookLevel(price=float(value["price"]), quantity=float(value["quantity"]))
    price, quantity = value
    return BookLevel(price=float(price), quantity=float(quantity))


def _time(row: Mapping[str, Any], *fields: str) -> datetime:
    for field in fields:
        parsed = _time_or_none(row.get(field))
        if parsed is not None:
            return parsed
    raise RecordFormatError(f"Record has no usable timestamp in {fields}")


def _time_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(timezone.utc).isoformat()


_BUILDERS = {
    ORDER_BOOK_KIND: order_book_from_record,
    PUBLIC_TRADE_KIND: public_trade_from_record,
    BAR_KIND: bar_from_record,
}
