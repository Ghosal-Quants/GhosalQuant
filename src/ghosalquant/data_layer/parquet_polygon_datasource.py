from datetime import datetime, timezone, time
from typing import AsyncIterable
import asyncio

import pandas as pd
from pandas import DataFrame

from ..core.types import Bar
from ..util.config import Config, ConfigError, ALLOWED_TFS, DataSrcType
from ..data_layer.datasource_base import DataSource
from .dataframe_datasource import DataFrameDataSource

from . import staticUpdater
from .staticUpdater import update_main_from_following

# I must confess, this class is nasty and 1/3 alcohol, 1/3 GPT, and 1/3 late night coding
# I avoid GPT at all costs usually, but pandas is so cancerous, so my apologies
class ParquetPolygonDataSource(DataSource):
    def __init__(self, config: Config) -> None:
        if config.data.source is not DataSrcType.POLYGON:
            raise ConfigError("Configuration file incorrectly set up for Polygon data.")

        if config.data.polygon.parquet_path is None:
            raise ConfigError("Parquet config is required in config.yaml, but was not found.")

        self._config = config

        if not self._config.system.symbols:
            raise ConfigError("Minimum one symbol must be specified in config.yaml.")

    async def __aiter__(self) -> AsyncIterable[Bar]:
        if self._config.system.fresh_data:
            # The downloader is a module of constants, so point it at this run's config first.
            staticUpdater.configure(self._config.data.static)
            update_main_from_following()

        # ----- get minimal cols ----- #
        path: str = self._config.data.polygon.parquet_path
        df: DataFrame = pd.read_parquet(path, columns=["ticker", "t", "o", "h", "l", "c", "v"])

        # ----- filter symbols ----- #
        symbols = set(self._config.system.symbols)
        mask = df["ticker"].isin(symbols)
        df = df[mask].copy()

        df["t"] = pd.to_datetime(df["t"], unit="ms", utc=True) # Unix time to datetime

        # ----- date range filter ----- #
        tr = self._config.time_range
        start_dt = datetime.combine(tr.start_date, time(0, 0), tzinfo=timezone.utc)
        end_dt   = datetime.combine(tr.end_date, time(23, 59, 59), tzinfo=timezone.utc)
        df = df[(df["t"] >= start_dt) & (df["t"] <= end_dt)]

        # ----- session time filter ----- #
        start_t: time = tr.session_start_time
        end_t: time   = tr.session_end_time
        tod = df["t"].dt.time
        if start_t <= end_t:
            df = df[(tod >= start_t) & (tod <= end_t)]
        else:
            # overnight session
            df = df[(tod >= start_t) | (tod <= end_t)]

        # ----- resample if needed ----- #
        exec_tf = self._config.system.execution_timeframe

        if exec_tf not in ALLOWED_TFS:
            raise ConfigError(f"Unrecognised timeframe: {exec_tf}")

        if exec_tf != self._config.data.polygon.base_resolution:
            df = self._resample(df, exec_tf)

        df = df.sort_values(["t", "ticker"], kind="stable")

        inner = DataFrameDataSource(
            dataframe=df,
            symbol_col="ticker",
            timestamp_col="t",
            open_col="o",
            high_col="h",
            low_col="l",
            close_col="c",
            volume_col="v",
        )

        async for bar in inner:
            yield bar

    # ----- Helpers ----- #
    @staticmethod
    def _resample(df: DataFrame, timeframe: str) -> DataFrame:
        df = df.set_index("t")
        out = (
            df.groupby("ticker")
            .resample(timeframe)
            .agg({
                "o": "first",
                "h": "max",
                "l": "min",
                "c": "last",
                "v": "sum"
            })
            .dropna(subset=["o", "h", "l", "c"])
            .reset_index()
        )
        return out


