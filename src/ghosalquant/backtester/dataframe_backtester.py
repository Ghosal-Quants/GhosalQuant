import asyncio
from datetime import datetime
from pandas import DataFrame
import pandas as pd

from ..backtester.backtester import Backtester
from ..core.types import SignalType, Signal
from ..execution.strategy_base import Strategy
from ..data_layer.dataframe_datasource import DataFrameDataSource
from ..execution.sinks import DataFrameSink, BacktesterSink
from ..portfolio.basic_portfolio import BasicPortfolio
from ..util.backtester_utils import calculate_stats, output_to_excel
from ..util.config import load_config

class DataFrameBacktester(Backtester):
    def __init__(
            self,
            strategy: Strategy,
            dataframe: DataFrame,
            config_path: str,

            timestamp_col: str | None = None,
            # DataFrame format:
            *,
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
        config = load_config(config_path)

        # Duplicate the initial DataFrame and initialise signal and confidence columns
        self._return_df = dataframe.copy()
        self._return_df[signal_col] = pd.NA
        self._return_df[confidence_col] = 0.0
        self._return_df[quantity_col] = 0.0

        # Backtesting variables
        self._symbols: list[str] = config.system.symbols
        self._portfolio = BasicPortfolio(init_capital=init_capital)

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=dataframe,
                symbol= config.system.symbols[0],
                timestamp_col= timestamp_col,
                open_col= open_col,
                high_col= high_col,
                low_col= low_col,
                close_col= close_col,
                volume_col= volume_col
            ),
            config= config,
            signal_destination=DataFrameSink(
                dataframe= self._return_df,
                timestamp_col= timestamp_col,
                signal_col= signal_col,
                confidence_col= confidence_col,
                quantity_col= quantity_col,
                next_sink=BacktesterSink(self)
            )
        )

    def get_return_dataframe(self) -> DataFrame:
        return self._return_df

    async def handle_signal(self, signal: Signal) -> None:
        if signal.signal_type is None:
            return

        if signal.quantity is None:
            raise ValueError("Signal quantity cannot be None")

        await self._portfolio.process_signal(signal=signal)

        if (signal.signal_type == SignalType.SELL) or (signal.signal_type == SignalType.BUY):
            self._portfolio.snapshot_equity(
                time_stamp=signal.time_stamp,
                prices={signal.symbol: signal.price},
            )


    def run(self) -> DataFrame:
        # ExecutionSystem.run() is an async routine so it must be run
        # with asyncio.run() to start the event loop
        asyncio.run(self.execution_system.run())

        #if self.df_results is not None:
        #    calculate_stats(df_results=self.df_results)
        #    output_to_excel(df_results=self.df_results)

        return self._return_df
