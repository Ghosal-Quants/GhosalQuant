import asyncio
from datetime import datetime, timezone
from typing import Any, AsyncIterable, Iterable

from ghosalquant.core.contracts import OCC_LENGTH
from ghosalquant.core.types import Bar, BookLevel, MarketEvent, OrderBook, PublicTrade
from ghosalquant.data_layer.datasource_base import DataSource

# Alpaca's quote channel is a one-level ``OrderBook``.
ALPACA_EVENT_KINDS = ("bars", "order_books", "trades")
STOCK_FEEDS = ("iex", "sip", "delayed_sip")
OPTION_FEEDS = ("opra", "indicative")

_INSTALL_HINT = (
    "Alpaca support needs the official SDK, which is an optional dependency: "
    "pip install 'ghosalquant[alpaca]'"
)


def to_occ(symbol: str) -> str:
    """
    Alpaca's option symbol in the padded OCC form the registry is keyed by.

    Alpaca writes ``AAPL240315C00172500``; the OCC pads the root to six.
    """
    text = symbol.strip().upper()
    if len(text) >= OCC_LENGTH or len(text) < 15:
        return text
    root, rest = text[:-15], text[-15:]
    return f"{root:<6}{rest}"


def to_alpaca(symbol: str) -> str:
    """The same contract as Alpaca writes it: the padding taken back out."""
    text = symbol.strip().upper()
    return f"{text[:6].strip()}{text[6:]}" if len(text) == OCC_LENGTH else text


class LiveAlpacaDataSource(DataSource):
    """
    Live data from Alpaca, through the official ``alpaca-py`` SDK.

    ``order_books`` is Alpaca's quote channel, which is top of book only. ``feed`` also chooses
    the market: ``iex``/``sip``/``delayed_sip`` are equities, ``opra``/``indicative`` are options
    and carry no bars. The SDK rather than a raw socket, because the option stream is msgpack.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: Iterable[str],
        *,
        events: Iterable[str] = ("bars",),
        feed: str = "iex",
        queue_size: int = 1024,
    ) -> None:
        symbols = tuple(sym.strip().upper() for sym in symbols if sym and sym.strip())
        if not symbols:
            raise ValueError("At least one valid symbol must be provided.")
        if not (api_key or "").strip() or not (api_secret or "").strip():
            raise ValueError("A valid Alpaca api_key and api_secret must be provided.")

        self.feed = feed.strip().lower()
        if self.feed not in STOCK_FEEDS + OPTION_FEEDS:
            raise ValueError(f"Unsupported Alpaca feed {feed!r}; use {STOCK_FEEDS + OPTION_FEEDS}")

        self.symbols = tuple(to_occ(sym) for sym in symbols)
        self.events = _normalise_event_kinds(events, ALPACA_EVENT_KINDS)
        if self.is_option_feed and "bars" in self.events:
            raise ValueError(f"The {self.feed} option feed carries quotes and trades, not bars.")

        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._queue_size = queue_size

    @property
    def is_option_feed(self) -> bool:
        return self.feed in OPTION_FEEDS

    def _build_stream(self):
        try:
            from alpaca.data.enums import DataFeed, OptionsFeed
            from alpaca.data.live import OptionDataStream, StockDataStream
        except ImportError as exc:  # pragma: no cover - depends on what is installed
            raise ImportError(_INSTALL_HINT) from exc

        if self.is_option_feed:
            return OptionDataStream(self._api_key, self._api_secret, feed=OptionsFeed(self.feed))
        return StockDataStream(self._api_key, self._api_secret, feed=DataFeed(self.feed))

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        stream = self._build_stream()
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        symbols = tuple(to_alpaca(sym) for sym in self.symbols)

        def handler(parse):
            async def handle(raw: Any) -> None:
                event = parse(raw)
                if event is not None:
                    # Dropping is better than unbounded memory when a strategy falls behind.
                    if queue.full():
                        queue.get_nowait()
                    queue.put_nowait(event)
            return handle

        if "order_books" in self.events:
            stream.subscribe_quotes(handler(self._parse_quote), *symbols)
        if "trades" in self.events:
            stream.subscribe_trades(handler(self._parse_trade), *symbols)
        if "bars" in self.events:
            stream.subscribe_bars(handler(self._parse_bar), *symbols)

        # run() calls asyncio.run, unusable inside a running loop; this is what it awaits.
        pump = asyncio.create_task(stream._run_forever())
        try:
            while True:
                getter = asyncio.ensure_future(queue.get())
                done, _ = await asyncio.wait(
                    {getter, pump}, return_when=asyncio.FIRST_COMPLETED
                )
                if getter in done:
                    yield getter.result()
                    continue
                getter.cancel()
                pump.result()      # the stream stopped: surface whatever stopped it
                return
        finally:
            pump.cancel()
            try:
                await stream.stop_ws()
            except Exception:      # pragma: no cover - the socket may already be gone
                pass

    # ----- Alpaca's models into ours ----- #

    @staticmethod
    def _parse_bar(raw: Any) -> Bar | None:
        at = _timestamp(getattr(raw, "timestamp", None))
        symbol = getattr(raw, "symbol", None)
        if not symbol or at is None:
            return None
        return Bar(
            time_stamp=at, symbol=to_occ(symbol),
            open=float(raw.open), high=float(raw.high), low=float(raw.low),
            close=float(raw.close), volume=float(raw.volume or 0.0),
        )

    @staticmethod
    def _parse_quote(raw: Any) -> OrderBook | None:
        at = _timestamp(getattr(raw, "timestamp", None))
        symbol = getattr(raw, "symbol", None)
        bid, ask = float(raw.bid_price or 0.0), float(raw.ask_price or 0.0)
        if not symbol or at is None or bid <= 0.0 or ask <= 0.0:
            return None
        return OrderBook(
            time_stamp=at, symbol=to_occ(symbol),
            bids=(BookLevel(bid, float(raw.bid_size or 0.0)),),
            asks=(BookLevel(ask, float(raw.ask_size or 0.0)),),
        )

    @staticmethod
    def _parse_trade(raw: Any) -> PublicTrade | None:
        at = _timestamp(getattr(raw, "timestamp", None))
        symbol = getattr(raw, "symbol", None)
        price = float(getattr(raw, "price", 0.0) or 0.0)
        if not symbol or at is None or price <= 0.0:
            return None
        trade_id = getattr(raw, "id", None)
        return PublicTrade(
            time_stamp=at, symbol=to_occ(symbol), price=price,
            quantity=float(getattr(raw, "size", 0.0) or 0.0),
            trade_id=None if trade_id is None else str(trade_id),
        )


def _timestamp(raw: Any) -> datetime | None:
    if not isinstance(raw, datetime):
        return None
    return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)


def _normalise_event_kinds(events: Iterable[str], allowed: tuple[str, ...]) -> tuple[str, ...]:
    kinds = tuple(dict.fromkeys(str(kind).strip().lower() for kind in events if str(kind).strip()))
    if not kinds:
        raise ValueError(f"At least one event kind is required; use {allowed}")
    unknown = [kind for kind in kinds if kind not in allowed]
    if unknown:
        raise ValueError(f"Unsupported event kinds {unknown}; use {allowed}")
    return kinds
