import asyncio
import math
import sys
from datetime import datetime, timezone
from typing import AsyncIterable, Iterable

from ib_insync import IB, Stock, Contract

from ghosalquant.core.types import Bar
from ghosalquant.data_layer.datasource_base import DataSource


class LiveIBKRDataSource(DataSource):
    """
    Defaults to live data (type 1). If a missing live data subscription, it steps down to delayed data (type 3).

    NOTE: Uses a different client_id than the IBKRBroker to avoid conflicts.
    - IBKRBroker uses client_id=1 by default, while LiveIBKRDataSource uses client_id=2 by default.
    """
    def __init__(
            self,
            symbols: Iterable[str | Contract],
            host: str = "127.0.0.1",
            port: int = 7497,
            client_id: int = 2,
            bar_resolution_sec: int = 1
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

        self.symbols = tuple(c.symbol for c in self._contracts)
        self.host = host
        self.port = port
        self.client_id = client_id
        self.bar_resolution_sec = bar_resolution_sec
        self.ib = IB()

    async def __aiter__(self) -> AsyncIterable[Bar]:
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

        # tell IBKR to start streaming data
        tickers = [self.ib.reqMktData(c, "", False, False) for c in self._contracts]

        queue = asyncio.Queue()
        last_emitted: dict[str, datetime | None] = {sym: None for sym in self.symbols} # last emit time / symbol
        
        def on_pending_tickers(pending_tickers):
            now = datetime.now(timezone.utc)
            for t in pending_tickers:
                sym = t.contract.symbol
                
                last_time = last_emitted.get(sym)
                if last_time is not None and (now - last_time).total_seconds() < self.bar_resolution_sec:
                    continue
                
                if math.isnan(t.last) or t.last <= 0:
                    raise ValueError(f"Received invalid last price {t.last} for {sym}")
                if math.isnan(t.open) or t.open <= 0:
                    raise ValueError(f"Received invalid open price {t.open} for {sym}")
                if math.isnan(t.high) or t.high <= 0:
                    raise ValueError(f"Received invalid high price {t.high} for {sym}")
                if math.isnan(t.low) or t.low <= 0:
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
                self.ib.cancelMktData(c)
            self.ib.disconnect()
