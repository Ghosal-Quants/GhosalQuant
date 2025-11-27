from core.types import Bar
from data_layer.data import DataSource
from datetime import datetime, timedelta
from typing import AsyncIterable
import yfinance as yf
import pandas as pd
import asyncio

class YFinanceDataSource(DataSource):
    def __init__(
            self,
            symbol: str,
            *,

            # --- Multiple init options ---
            # 1. Pass datetimes
            start: str | datetime | None = None,
            end: str | datetime | None = None,

            # 2. Pass Y/M/D as integers
            start_year: int | None = None,
            start_month: int | None = None,
            start_day: int | None = None,

            end_year: int | None = None,
            end_month: int | None = None,
            end_day: int | None = None
    ) -> None:
        self.symbol = symbol

        # Find start datetime
        if start is not None:
            self.start = start
        elif None not in (start_year, start_month, start_day):
            self.start = datetime(year=start_year, month=start_month, day=start_day)
        else: # Default to last 30 days if no start is provided
            self.start = datetime.now() - timedelta(days=30)

        # Find end datetime
        if end is not None:
            self.end = end
        elif None not in (end_year, end_month, end_day):
            self.end = datetime(year=end_year, month=end_month, day=end_day)
        else: # Default to now if no end is provided
            self.end = datetime.now()

    async def __aiter__(self) -> AsyncIterable[Bar]:
        print(
            f"Fetching {self.symbol} data from {self.start:%Y-%m-%d} to {self.end:%Y-%m-%d}..."
        )

        data: pd.DataFrame = yf.download(self.symbol, start=self.start, end=self.end, progress=False, auto_adjust=True)
        
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
