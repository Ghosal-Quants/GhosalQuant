import asyncio
import json
import sys
from datetime import datetime, timezone
from typing import AsyncIterable, Iterable
import websockets

from ghosalquant.core.types import Bar
from ghosalquant.data_layer.datasource_base import DataSource


class LivePolygonDataSource(DataSource):
    def __init__(
            self,
            api_key: str,
            symbols: Iterable[str],
            base_resolution: str = "1MIN", # 1SEC, 1MIN, ...
            *,
            websocket_url: str = "wss://socket.massive.com/stocks",
            reconnect_base_sec: float = 0.5,
            reconnect_max_sec: float = 10.0
    ):
        symbols = tuple(sym.strip().upper() for sym in symbols if sym and sym.strip())
        if not symbols:
            raise ValueError("At least one valid symbol must be provided.")

        api_key = api_key.strip() if api_key else None
        if not api_key:
            raise ValueError("A valid API key must be provided.")

        self._api_key = api_key
        self._symbols = symbols
        self.base_resolution = base_resolution.strip().upper()

        self._websocket_url = websocket_url
        self._reconnect_base_sec = reconnect_base_sec
        self._reconnect_max_sec = reconnect_max_sec

        # TODO: Allow more resolutions by aggregating into nSEC or nMIN bars
        if self.base_resolution not in {"1SEC", "1MIN"}:
            raise ValueError(f"Unsupported base_resolution: {self.base_resolution} (use 1SEC or 1MIN)")

    async def __aiter__(self) -> AsyncIterable[Bar]:
        backoff = self._reconnect_base_sec

        while True:
            try:
                async for bar in self._stream_session():
                    backoff = self._reconnect_base_sec
                    yield bar
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

    async def _stream_session(self) -> AsyncIterable[Bar]:
        async with websockets.connect(
            self._websocket_url,
            max_queue=64
        ) as ws:

            # authenticate
            await ws.send(json.dumps({"action": "auth", "params": self._api_key}))

            # subscribe to bars for each symbol
            if self.base_resolution == "1MIN":
                channel = "AM"
            elif self.base_resolution == "1SEC":
                channel = "A"
            else:
                raise ValueError(f"Unsupported base_resolution: {self.base_resolution}")

            subscriptions = [f"{channel}.{sym}" for sym in self._symbols]
            params = ",".join(subscriptions)

            await ws.send(json.dumps({
                "action": "subscribe",
                "params": params
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

                    if event.get("ev") in {"A", "AM"}:
                        bar = self._parse_agg(event)
                        if bar is not None:
                            yield bar

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
        ts = datetime.fromtimestamp(int(end_ms) / 1000.0, tz=timezone.utc)

        o = event.get("o")
        h = event.get("h")
        l = event.get("l")
        c = event.get("c")
        v = event.get("v")

        if any(x is None for x in [ts, o, h, l, c, v]):
            return None

        return Bar(ts, symbol, float(o), float(h), float(l), float(c), int(v))
