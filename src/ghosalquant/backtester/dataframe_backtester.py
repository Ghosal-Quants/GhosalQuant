import asyncio
from typing import Iterable

from pandas import DataFrame
import pandas as pd

from ..backtester.backtester_base import Backtester, _resolve_config
from ..brokerage.builder import build_broker
from ..core.types import Order, EquityPoint, BacktestKPMs
from ..execution.strategy_base import Strategy
from ..data_layer.dataframe_datasource import DataFrameDataSource
from ..execution.sinks import DataFrameSink
from ..portfolio.basic_portfolio import BasicPortfolio
from ..util.backtester_utils import output_to_excel, calculate_kpms
from ..util.config import Config

class DataFrameBacktester(Backtester):
    def __init__(
            self,
            strategy: Strategy,
            dataframe: DataFrame,
            config_path: str | None = None,

            timestamp_col: str | None = None,
            # DataFrame format:
            *,
            config: Config | None = None,
            symbol: str | None = None,
            open_col: str   = "Open",
            high_col: str   = "High",
            low_col: str    = "Low",
            close_col: str  = "Close",
            volume_col: str = "Volume",

            # Additional Columns
            signal_col: str = "Signal",
            confidence_col: str = "Confidence",
            quantity_col: str = "Quantity",

            # Backtesting
            init_capital: float = 10_000.0
    ) -> None:
        """
        Backtest one symbol's bars from ``dataframe``, which supplies the dates as well.

        Configured by ``config``, else the YAML at ``config_path``, else ``Config()``; DATA and
        TIME_RANGE are not needed. The bars are traded as ``symbol``, else the first configured symbol.
        """
        config = _resolve_config(config, config_path)
        symbol = symbol or config.system.symbols[0]

        df = dataframe.copy()

        if isinstance(df.columns, pd.MultiIndex): # Flatten MultiIndex if present
            required = {open_col, high_col, low_col, close_col, volume_col}

            levels = [set(map(str, df.columns.get_level_values(i))) for i in range(df.columns.nlevels)]
            level_with_required = None
            for i, vals in enumerate(levels):
                if required.issubset(vals):
                    level_with_required = i
                    break

            if level_with_required is not None:
                df.columns = df.columns.get_level_values(level_with_required)

        # Duplicate the initial DataFrame and initialise signal and confidence columns
        self._return_df = df
        self._return_df[signal_col] = pd.NA
        self._return_df[confidence_col] = 0.0
        self._return_df[quantity_col] = 0.0

        # Backtesting variables
        self._symbols: list[str] = [symbol]
        self._portfolio = BasicPortfolio(init_capital=init_capital)

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=df,
                symbol= symbol,
                timestamp_col= timestamp_col,
                open_col= open_col,
                high_col= high_col,
                low_col= low_col,
                close_col= close_col,
                volume_col= volume_col
            ),
            signal_destination=DataFrameSink(
                dataframe= self._return_df,
                timestamp_col= timestamp_col,
                signal_col= signal_col,
                confidence_col= confidence_col,
                quantity_col= quantity_col
            ),
            portfolio= self._portfolio,
            broker=build_broker(config.broker),
            config= config,
        )

    @property
    def dataframe(self) -> DataFrame:
        return self._return_df
