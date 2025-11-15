from core.types import Bar
from data_layer.data import DataSource
from datetime import datetime
from typing import AsyncIterable
import asyncio
from pandas import DataFrame

class DataFrameDataSource(DataSource):
    def __init__(
            self,
            dataframe: DataFrame,
            *,
            symbol: str,
            timestamp_col: str | None = None,
            open_col: str = "Open",
            high_col: str = "High",
            low_col: str = "Low",
            close_col: str = "Close",
            volume_col: str = "Volume",
    ) -> None:
        self.df = dataframe
        self.symbol = symbol
        self.timestamp_col = timestamp_col
        self.cols = {
            "open": open_col,
            "high": high_col,
            "low": low_col,
            "close": close_col,
            "volume": volume_col,
        }

    @staticmethod
    def _scalar(x):
        return x.iloc[0] if hasattr(x, "iloc") else x


    async def __aiter__(self) -> AsyncIterable[Bar]:
        for index, row in self.df.iterrows():

            if self.timestamp_col is not None:
                ts = row[self.timestamp_col]
            else:
                ts = index

            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()

            yield Bar(
                time_stamp=ts,
                symbol=self.symbol,
                open=float(self._scalar(row[self.cols["open"]])),
                high=float(self._scalar(row[self.cols["high"]])),
                low=float(self._scalar(row[self.cols["low"]])),
                close=float(self._scalar(row[self.cols["close"]])),
                volume=float(self._scalar(row[self.cols["volume"]])),
            )
