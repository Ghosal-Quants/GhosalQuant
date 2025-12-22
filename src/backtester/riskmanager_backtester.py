import asyncio
from doctest import UnexpectedException

from pandas import DataFrame

from backtester.backtester import Backtester
from core.types import SignalType, Signal
from execution.strategy_base import Strategy
from data_layer.dataframe_datasource import DataFrameDataSource
from execution.sinks import DataFrameSink, RiskManagerSink
from util.deprecation import deprecated


@deprecated("Risk management is now handled by a separate module. Use DataFrameBacktester or another Backtester instead.")
class RiskManagerBacktester(Backtester):
    def __init__(
        self,
        strategy: Strategy,
        dataframe: DataFrame,
        timestamp_col: str | None = None,
        *,
        symbol: str,
        open_col: str   = "Open",
        high_col: str   = "High",
        low_col: str    = "Low",
        close_col: str  = "Close",
        volume_col: str = "Volume",
        # Additional columns
        signal_col: str = "Signal",
        confidence_col: str = "Confidence",
        quantity_col: str = "Quantity"
    ) -> None:
        # Duplicate the initial DataFrame and initialise the columns
        self._return_df = dataframe.copy()
        self._return_df[signal_col] = SignalType.NOOP.value
        self._return_df[confidence_col] = 0.0
        self._return_df[quantity_col] = 0.0

        # Initialise DataFrameSink
        df_sink = DataFrameSink(
            dataframe=self._return_df,
            timestamp_col=timestamp_col,
            signal_col=signal_col,
            confidence_col=confidence_col,
            quantity_col=quantity_col
        )

        # Initialise RiskManagerSink with DataFrameSink as next_sink
        rm_sink = RiskManagerSink(next_sink=df_sink)

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=dataframe,
                symbol=symbol,
                timestamp_col=timestamp_col,
                open_col=open_col,
                high_col=high_col,
                low_col=low_col,
                close_col=close_col,
                volume_col=volume_col
            ),
            signal_destination=rm_sink
        )

    async def handle_signal(self, signal: Signal) -> None:
        raise UnexpectedException("RiskManagerBacktester.handle_signal() should not be called.")

    def run(self) -> DataFrame:
        # Run the async execution system and return the updated DataFrame
        asyncio.run(self.execution_system.run())
        return self._return_df