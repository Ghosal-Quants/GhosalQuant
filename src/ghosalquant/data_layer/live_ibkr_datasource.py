import asyncio
import math
import sys
from datetime import datetime, timezone
from typing import AsyncIterable, Iterable, Sequence

from ib_insync import IB, Stock, Contract

from ghosalquant.core.types import Bar, BookLevel, MarketEvent, OrderBook, PublicTrade
from ghosalquant.data_layer.datasource_base import DataSource


IBKR_EVENT_KINDS = ("bars", "order_books", "trades")
DEFAULT_DEPTH_ROWS = 5


class LiveIBKRDataSource(DataSource):
    """
    Defaults to live data (type 1). If a missing live data subscription, it steps down to delayed data (type 3).

    ``events`` selects what is streamed:
    - ``"bars"``: ``reqMktData`` ticks throttled into ``Bar`` objects every ``bar_resolution_sec`` (the original
      behaviour);
    - ``"order_books"``: ``reqMktDepth`` with ``depth`` rows per side, emitted as ``OrderBook`` objects at most every
      ``book_resolution_sec`` (0 = every update);
    - ``"trades"``: ``reqTickByTickData(..., "AllLast")``, emitted as ``PublicTrade`` objects.

    NOTE: Uses a different client_id than the IBKRBroker to avoid conflicts.
    - IBKRBroker uses client_id=1 by default, while LiveIBKRDataSource uses client_id=2 by default.
    """
    def __init__(
            self,
            symbols: Iterable[str | Contract],
            host: str = "127.0.0.1",
            port: int = 7497,
            client_id: int = 2,
            bar_resolution_sec: int = 1,
            *,
            events: Iterable[str] = ("bars",),
            depth: int | None = None,
            book_resolution_sec: float = 0.0,
    ):
        self._contracts = []
        for sym in symbols:
            if isinstance(sym, Contract):
                self._contracts.append(sym)
            elif isinstance(sym, str) and sym.strip(): # Assume US stock
                self._contracts.append(Stock(sym.strip().upper(), 'SMART', 'USD'))
            else:
                raise ValueError(f"Invalid symbol or contract provided: {sym}")

        if not self._contracts:
            raise ValueError("At least one valid symbol or contract must be provided.")

        self.events = _normalise_event_kinds(events, IBKR_EVENT_KINDS)
        if depth is not None and depth <= 0:
            raise ValueError("depth must be > 0")
        if book_resolution_sec < 0.0:
            raise ValueError("book_resolution_sec must be >= 0")

        self.symbols = tuple(c.symbol for c in self._contracts)
        self.host = host
        self.port = port
        self.client_id = client_id
        self.bar_resolution_sec = bar_resolution_sec
        self.depth = depth if depth is not None else (DEFAULT_DEPTH_ROWS if "order_books" in self.events else None)
        self.book_resolution_sec = book_resolution_sec
        self.ib = IB()

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        if not self.ib.isConnected():
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)

        def on_error(reqId, errorCode, errorString, contract):
            if errorCode in {162, 354, 10089, 10090}:
                print(f"[IBKR] Missing live data subscription (Error {errorCode}). Stepping down to delayed data (Type 3).", file=sys.stderr)
                self.ib.reqMarketDataType(3)
            else:
                print(f"[IBKR] Error {errorCode}: {errorString}", file=sys.stderr)

        self.ib.errorEvent += on_error
        self.ib.reqMarketDataType(1)

        await self.ib.qualifyContractsAsync(*self._contracts)

        want_bars = "bars" in self.events
        want_books = "order_books" in self.events
        want_trades = "trades" in self.events

        # tell IBKR to start streaming data
        if want_bars:
            for c in self._contracts:
                self.ib.reqMktData(c, "", False, False)
        if want_books:
            for c in self._contracts:
                self.ib.reqMktDepth(c, numRows=self.depth)
        if want_trades:
            for c in self._contracts:
                self.ib.reqTickByTickData(c, "AllLast")

        queue = asyncio.Queue()
        last_emitted: dict[str, datetime | None] = {sym: None for sym in self.symbols} # last bar emit time / symbol
        last_book_emitted: dict[str, datetime | None] = {sym: None for sym in self.symbols}

        def on_pending_tickers(pending_tickers):
            now = datetime.now(timezone.utc)
            for t in pending_tickers:
                sym = t.contract.symbol

                if want_trades and t.tickByTicks:
                    for trade in trades_from_ticks(sym, t.tickByTicks, fallback_time=now):
                        queue.put_nowait(trade)

                if want_books and (t.domBids or t.domAsks):
                    last_book = last_book_emitted.get(sym)
                    if last_book is None or (now - last_book).total_seconds() >= self.book_resolution_sec:
                        book = book_from_ticker(t, symbol=sym, time_stamp=now, depth=self.depth)
                        if book is not None:
                            last_book_emitted[sym] = now
                            queue.put_nowait(book)

                if not want_bars:
                    continue

                last_time = last_emitted.get(sym)
                if last_time is not None and (now - last_time).total_seconds() < self.bar_resolution_sec:
                    continue
                if math.isnan(t.last) or math.isnan(t.open) or math.isnan(t.high) or math.isnan(t.low):
                    continue

                if t.last <= 0:
                    raise ValueError(f"Received invalid last price {t.last} for {sym}")
                if t.open <= 0:
                    raise ValueError(f"Received invalid open price {t.open} for {sym}")
                if t.high <= 0:
                    raise ValueError(f"Received invalid high price {t.high} for {sym}")
                if t.low <= 0:
                    raise ValueError(f"Received invalid low price {t.low} for {sym}")

                bar = Bar(
                    time_stamp=now,
                    symbol=sym,
                    open=t.open,
                    high=t.high,
                    low=t.low,
                    close=t.last,
                    volume=-1.0 if math.isnan(t.volume) or t.volume < 0 else t.volume
                )

                last_emitted[sym] = now
                queue.put_nowait(bar)

        self.ib.pendingTickersEvent += on_pending_tickers

        try:
            while True:
                yield await queue.get()
        finally:
            self.ib.errorEvent -= on_error
            self.ib.pendingTickersEvent -= on_pending_tickers
            for c in self._contracts:
                if want_bars:
                    self.ib.cancelMktData(c)
                if want_books:
                    self.ib.cancelMktDepth(c)
                if want_trades:
                    self.ib.cancelTickByTickData(c, "AllLast")
            self.ib.disconnect()


def book_from_ticker(ticker, *, symbol: str, time_stamp: datetime, depth: int | None = None) -> OrderBook | None:
    """
    Build an ``OrderBook`` from a ticker's ``domBids`` / ``domAsks``.

    Deep books can list several market makers at one price; those rows are aggregated so every
    level has a unique price, then sorted best first and cut to ``depth`` rows per side.
    """
    bids = _aggregate_levels(getattr(ticker, "domBids", None) or (), descending=True, depth=depth)
    asks = _aggregate_levels(getattr(ticker, "domAsks", None) or (), descending=False, depth=depth)
    if not bids and not asks:
        return None
    return OrderBook(time_stamp=time_stamp, symbol=symbol, bids=bids, asks=asks)


def trades_from_ticks(symbol: str, ticks: Sequence, *, fallback_time: datetime) -> list[PublicTrade]:
    """Convert ``TickByTickAllLast`` ticks to ``PublicTrade`` objects, skipping zero-size or zero-price ticks."""
    trades: list[PublicTrade] = []
    for tick in ticks:
        price = float(getattr(tick, "price", 0.0) or 0.0)
        size = float(getattr(tick, "size", 0.0) or 0.0)
        if price <= 0.0 or size <= 0.0:
            continue
        when = getattr(tick, "time", None)
        if not isinstance(when, datetime):
            when = fallback_time
        trades.append(PublicTrade(time_stamp=when, symbol=symbol, price=price, quantity=size))
    return trades


def _aggregate_levels(rows, *, descending: bool, depth: int | None) -> tuple[BookLevel, ...]:
    size_by_price: dict[float, float] = {}
    for row in rows:
        price = float(getattr(row, "price", 0.0) or 0.0)
        size = float(getattr(row, "size", 0.0) or 0.0)
        if price <= 0.0 or size <= 0.0:
            continue
        size_by_price[price] = size_by_price.get(price, 0.0) + size
    prices = sorted(size_by_price, reverse=descending)
    if depth is not None:
        prices = prices[:depth]
    return tuple(BookLevel(price, size_by_price[price]) for price in prices)


def _normalise_event_kinds(events: Iterable[str], allowed: tuple[str, ...]) -> tuple[str, ...]:
    kinds = tuple(dict.fromkeys(kind.strip().lower() for kind in events if kind and kind.strip()))
    if not kinds:
        raise ValueError(f"events must include at least one of {allowed}")
    unknown = [kind for kind in kinds if kind not in allowed]
    if unknown:
        raise ValueError(f"Unsupported event kinds {unknown}; use {allowed}")
    return kinds
