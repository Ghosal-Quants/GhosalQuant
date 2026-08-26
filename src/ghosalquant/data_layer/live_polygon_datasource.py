import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import AsyncIterable, Iterable
import websockets

from ghosalquant.core.types import Bar, BookLevel, MarketEvent, OrderBook, PublicTrade
from ghosalquant.data_layer.datasource_base import DataSource


POLYGON_EVENT_KINDS = ("bars", "quotes", "trades")


class LivePolygonDataSource(DataSource):
    def __init__(
            self,
            api_key: str,
            symbols: Iterable[str],
            base_resolution: str = "1MIN", # 1SEC, 1MIN, ...
            *,
            events: Iterable[str] = ("bars",),
            websocket_url: str = "wss://delayed.massive.com/stocks",  # Default to 15 min delayed data
            reconnect_base_sec: float = 0.5,
            reconnect_max_sec: float = 10.0
    ):
        """
        A live data source that connects to Polygon's WebSocket API.

        ``events`` selects what is streamed: ``"bars"`` (aggregate channels ``A``/``AM`` -> ``Bar``),
        ``"quotes"`` (channel ``Q`` -> a one-level ``OrderBook`` per NBBO update), and ``"trades"``
        (channel ``T`` -> ``PublicTrade``). Sizes are passed through as the feed reports them.

        "wss://delayed.massive.com/stocks" is the endpoint for delayed data
        "wss://socket.massive.com/stocks" is the endpoint for delayed data
        """
        symbols = tuple(sym.strip().upper() for sym in symbols if sym and sym.strip())
        if not symbols:
            raise ValueError("At least one valid symbol must be provided.")

        api_key = api_key.strip() if api_key else None
        if not api_key:
            raise ValueError("A valid API key must be provided.")

        self._api_key = api_key
        self._symbols = symbols
        self.base_resolution = base_resolution.strip().upper()
        self.events = _normalise_event_kinds(events, POLYGON_EVENT_KINDS)

        self._websocket_url = websocket_url
        self._reconnect_base_sec = reconnect_base_sec
        self._reconnect_max_sec = reconnect_max_sec

        # TODO: Allow more resolutions by aggregating into nSEC or nMIN bars
        if self.base_resolution not in {"1SEC", "1MIN"}:
            raise ValueError(f"Unsupported base_resolution: {self.base_resolution} (use 1SEC or 1MIN)")

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        backoff = self._reconnect_base_sec

        while True:
            try:
                async for event in self._stream_session():
                    backoff = self._reconnect_base_sec
                    yield event
            except (
                    websockets.ConnectionClosed,
                    websockets.ConnectionClosedError,
                    websockets.ConnectionClosedOK,
                    OSError,
                    TimeoutError,
                    asyncio.TimeoutError,
            ):
                print("Polygon: Attempting to reconnect...", file=sys.stderr)
                await asyncio.sleep(backoff)
                backoff = min(self._reconnect_max_sec, backoff * 2)

    def subscription_params(self) -> str:
        """The comma-separated channel list sent in the subscribe message."""
        channels: list[str] = []
        if "bars" in self.events:
            channels.append("AM" if self.base_resolution == "1MIN" else "A")
        if "quotes" in self.events:
            channels.append("Q")
        if "trades" in self.events:
            channels.append("T")
        return ",".join(f"{channel}.{sym}" for channel in channels for sym in self._symbols)

    async def _stream_session(self) -> AsyncIterable[MarketEvent]:
        async with websockets.connect(
            self._websocket_url,
            max_queue=64
        ) as ws:

            # authenticate
            await ws.send(json.dumps({"action": "auth", "params": self._api_key}))

            await ws.send(json.dumps({
                "action": "subscribe",
                "params": self.subscription_params()
            }))

            authed = False

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)

                if isinstance(msg, dict): # Make sure msg is consistently a list
                    msg = [msg]

                for event in msg:
                    if not isinstance(event, dict):
                        continue

                    # status events
                    if event.get("ev") == "status":
                        m = (event.get("message") or "").lower()
                        s = (event.get("status") or "").lower()

                        if "auth_failed" == s:
                            raise RuntimeError(f"WebSocket authentication failed: {event}")
                        elif "auth_success" == s:
                            authed = True
                        elif "error" == s:
                            raise RuntimeError(f"WebSocket error: {m}")
                        continue # We can ignore things like "connected", etc.

                    if not authed:
                        continue

                    parsed = self.parse_event(event)
                    if parsed is not None:
                        yield parsed

    def parse_event(self, event: dict) -> MarketEvent | None:
        kind = event.get("ev")
        if kind in {"A", "AM"} and "bars" in self.events:
            return self._parse_agg(event)
        if kind == "Q" and "quotes" in self.events:
            return self._parse_quote(event)
        if kind == "T" and "trades" in self.events:
            return self._parse_trade(event)
        return None

    @staticmethod
    def _parse_agg(event: dict) -> Bar | None:
        """
        Currently only considers symbol, timestamp, and OHLCV.

        Example format input:
        {
        'ev': 'A',
        'sym': 'AAPL',
        'v': 165,
        'av': 33624103,
        'op': 271.78,
        'vw': 274.8394,
        'o': 274.8,
        'c': 274.8,
        'h': 274.8,
        'l': 274.8,
        'a': 273.7618,
        'z': 82,
        's': 1772061193000,
        'e': 1772061194000,
        'dv': '165.0'
        }
        """
        symbol = event.get("sym")
        if not symbol:
            return None

        # end timestamp becomes the bar timestamp
        end_ms = event.get("e")
        if end_ms is None:
            return None
        ts = _from_epoch_ms(end_ms)

        o = event.get("o")
        h = event.get("h")
        l = event.get("l")
        c = event.get("c")
        v = event.get("v")

        if any(x is None for x in [ts, o, h, l, c, v]):
            return None

        return Bar(ts, symbol, float(o), float(h), float(l), float(c), int(v))

    @staticmethod
    def _parse_quote(event: dict) -> OrderBook | None:
        """
        An NBBO quote becomes a one-level order book.

        Example: {'ev': 'Q', 'sym': 'AAPL', 'bx': 11, 'bp': 170.0, 'bs': 3, 'ax': 12, 'ap': 170.05, 'as': 5,
                  'c': 0, 't': 1610000000000, 'q': 12345}
        """
        symbol = event.get("sym")
        stamp = event.get("t")
        if not symbol or stamp is None:
            return None

        bids = _quote_side(event.get("bp"), event.get("bs"))
        asks = _quote_side(event.get("ap"), event.get("as"))
        if not bids and not asks:
            return None

        sequence = event.get("q")
        return OrderBook(
            time_stamp=_from_epoch_ms(stamp),
            symbol=symbol,
            bids=bids,
            asks=asks,
            sequence=None if sequence is None else int(sequence),
        )

    @staticmethod
    def _parse_trade(event: dict) -> PublicTrade | None:
        """
        Example: {'ev': 'T', 'sym': 'AAPL', 'i': '52983525029461', 'x': 4, 'p': 170.02, 's': 100, 'c': [14, 41],
                  't': 1610000000000, 'q': 12346, 'z': 3}
        """
        symbol = event.get("sym")
        stamp = event.get("t")
        price = event.get("p")
        size = event.get("s")
        if not symbol or stamp is None or price is None or size is None:
            return None
        if float(price) <= 0.0 or float(size) <= 0.0:
            return None

        trade_id = event.get("i")
        return PublicTrade(
            time_stamp=_from_epoch_ms(stamp),
            symbol=symbol,
            price=float(price),
            quantity=float(size),
            trade_id=None if trade_id is None else str(trade_id),
        )


def _normalise_event_kinds(events: Iterable[str], allowed: tuple[str, ...]) -> tuple[str, ...]:
    kinds = tuple(dict.fromkeys(kind.strip().lower() for kind in events if kind and kind.strip()))
    if not kinds:
        raise ValueError(f"events must include at least one of {allowed}")
    unknown = [kind for kind in kinds if kind not in allowed]
    if unknown:
        raise ValueError(f"Unsupported event kinds {unknown}; use {allowed}")
    return kinds


def _quote_side(price, size) -> tuple[BookLevel, ...]:
    if price is None or size is None:
        return ()
    price = float(price)
    size = float(size)
    if price <= 0.0 or size < 0.0:
        return ()
    return (BookLevel(price, size),)


def _from_epoch_ms(value) -> datetime:
    return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
