# from src.core.types import Bar

from core.types import Bar
from typing import Protocol, AsyncIterable
import yfinance as yf
import asyncio

class DataSource(Protocol):
    def __init__(self, symbol: str = "^GSPC"):
        self.symbol = symbol

    async def __aiter__(self) -> AsyncIterable[Bar]:
        year = 2020
        month = 1
        start = f"{year}-{month:02d}-01"
        end = f"{year}-{month:02d}-28"

        print(f"Fetching {self.symbol} data from {start} to {end}...")

        data = yf.download(self.symbol, start=start, end=end, progress=False, auto_adjust=True)
        
        for ts, row in data.iterrows():
            bar = Bar(
                time_stamp=ts.to_pydatetime(),
                symbol=self.symbol,
                open=float(row["Open"].item()),
                high=float(row["High"].item()),
                low=float(row["Low"].item()),
                close=float(row["Close"].item()),
                volume=float(row["Volume"].item()),
            )
            yield bar
            await asyncio.sleep(0.1)

