from ..core.types import Bar
from ..data_layer.datasource_base import DataSource

from datetime import datetime
from typing import AsyncIterable
import asyncio
from pandas import DataFrame

class DataFrameDataSource(DataSource):
    def __init__(
            self,
            dataframe: DataFrame,
            *,
            symbol: str | None = None,
            symbol_col: str | None = None,
            timestamp_col: str | None = None,
            open_col: str = "Open",
            high_col: str = "High",
            low_col: str = "Low",
            close_col: str = "Close",
            volume_col: str = "Volume",
    ) -> None:
        if (symbol is None or symbol.strip() == "") and symbol_col is None:
            raise ValueError("A symbol or symbol column (symbol_col) must be provided.")

        self.df = dataframe
        self.symbol = symbol
        self.symbol_col = symbol_col
        self.timestamp_col = timestamp_col
        self.cols = {
            "open": open_col,
            "high": high_col,
            "low": low_col,
            "close": close_col,
            "volume": volume_col,
        }

    async def __aiter__(self) -> AsyncIterable[Bar]:
        cols = list(self.df.columns)

        # precompute indices for faster access
        open_i: int   = cols.index(self.cols["open"])
        high_i: int   = cols.index(self.cols["high"])
        low_i: int    = cols.index(self.cols["low"])
        close_i: int  = cols.index(self.cols["close"])
        volume_i: int = cols.index(self.cols["volume"])

        sym_i: int | None = None
        ts_i: int | None = None

        if self.symbol_col is not None:
            sym_i: int = cols.index(self.symbol_col)

        if self.timestamp_col is not None:
            ts_i: int  = cols.index(self.timestamp_col)


        for tup in self.df.itertuples(index=True, name=None):
            index = tup[0]
            row = tup[1:]

            sym = row[sym_i] if sym_i is not None else self.symbol
            ts = row[ts_i] if ts_i is not None else index

            if hasattr(ts, "to_pydatetime"):
                ts = ts.to_pydatetime()

            yield Bar(
                time_stamp=ts,
                symbol=sym,
                open=float(row[open_i]),
                high=float(row[high_i]),
                low=float(row[low_i]),
                close=float(row[close_i]),
                volume=float(row[volume_i]),
            )
