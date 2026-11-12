import pandas as pd
from datetime import date, time
from typing import AsyncIterable

from .datasource_base import DataSource
from ..core.types import Bar

_YFINANCE_TFS: dict[str, str] = {
    "1Min": "1m",
    "2Min": "2m",
    "5Min": "5m",
    "15Min": "15m",
    "30Min": "30m",
    "1H": "60m",
    "90Min": "90m",
    "1D": "1d",
    "5D": "5d",
    "1W": "1wk",
    "1M": "1mo",
    "3M": "3mo",
}



def _is_intraday(interval: str) -> bool:
    return interval.endswith("m") or interval.endswith("h")

class YFinanceMultiDataSource(DataSource):
    def __init__(
        self,
        *,
        symbols: list[str],
        start: date,
        end: date,
        timeframe: str,
        session: tuple[time, time]
    ):
        if not symbols:
            raise ValueError("Symbols list cannot be empty.")

        self.symbols = symbols
        self.start = start
        self.end = end
        self.timeframe = timeframe
        self.session = session

    async def __aiter__(self) -> AsyncIterable[Bar]:
        if self.timeframe not in _YFINANCE_TFS:
            raise ValueError(f"Unsupported yfinance timeframe: {self.timeframe}. Supported: {sorted(_YFINANCE_TFS)}")
        interval = _YFINANCE_TFS[self.timeframe]

        # Imported here so the package loads where yfinance is not installed, such as in a browser.
        import yfinance as yf

        df = yf.download(
            tickers=" ".join(self.symbols),
            start=self.start,
            end=self.end,
            interval=interval,
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )

        if df is None or df.empty:
            return

        # single Index Case
        if not isinstance(df.columns, pd.MultiIndex):
            symbol = self.symbols[0]
            df_single = df

            if self.session is not None and _is_intraday(interval):
                s_start, s_end = self.session
                mask = (df_single.index.time >= s_start) & (df_single.index.time <= s_end)
                df_single = df_single.loc[mask]

            for ts, row in df_single.iterrows():
                yield Bar(
                    time_stamp=ts.to_pydatetime(),
                    symbol=symbol,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0.0)),
                )
            return

        # MultiIndex case
        l0 = set(map(str, df.columns.get_level_values(0)))
        if not any(sym in l0 for sym in self.symbols):
            raise ValueError(
                "Unexpected yfinance MultiIndex column layout. "
                "Expected level 0 to contain tickers because group_by='ticker'."
            )

        df_multi = df

        if self.session is not None and _is_intraday(interval):
            s_start, s_end = self.session
            mask = (df_multi.index.time >= s_start) & (df_multi.index.time <= s_end)
            df_multi = df_multi.loc[mask]

        available_symbols = [s for s in self.symbols if s in l0]

        for ts in df_multi.index:
            for symbol in available_symbols:
                row = df_multi[symbol].loc[ts]
                if pd.isna(row.get("Open")):
                    continue

                yield Bar(
                    time_stamp=ts.to_pydatetime(),
                    symbol=symbol,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0.0)),
                )
