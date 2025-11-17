import asyncio

from pandas import DataFrame

from backtester.backtester import Backtester
from core.types import SignalType, Signal
from execution.strategy_base import Strategy
from data_layer.dataframe_datasource import DataFrameDataSource

class DataFrameBacktester(Backtester):
    def __init__(
            self,
            strategy: Strategy,
            dataframe: DataFrame,

            timestamp_col: str | None = None,
            # DataFrame format:
            *,
            symbol: str,
            open_col: str   = "Open",
            high_col: str   = "High",
            low_col: str    = "Low",
            close_col: str  = "Close",
            volume_col: str = "Volume",

            # Additional Columns
            signal_col: str = "Signal",
            confidence_col: str = "Confidence"
    ) -> None:
        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=dataframe,
                symbol= symbol,
                timestamp_col= timestamp_col,
                open_col= open_col,
                high_col= high_col,
                low_col= low_col,
                close_col= close_col,
                volume_col= volume_col
            )
        )

        self._timestamp_col = timestamp_col
        self._signal_col = signal_col
        self._confidence_col = confidence_col

        # Duplicate the initial DataFrame and initialise signal and confidence columns
        self._return_df = dataframe.copy()
        self._return_df[self._signal_col] = SignalType.NOOP.value
        self._return_df[self._confidence_col] = 0.0

    async def handle_signal(self, signal: Signal) -> None:
        if signal.signal_type not in (SignalType.BUY, SignalType.SELL):
            return

        # Record signals into new column in the duplicate DataFrame
        if self._timestamp_col is not None:
            mask = self._return_df[self._timestamp_col] == signal.time_stamp
        else:
            mask = self._return_df.index == signal.time_stamp

        if not mask.any():
            print("[WARN]: No row found for signal timestamp:", signal.time_stamp)

        self._return_df.loc[mask, self._signal_col] = signal.signal_type.value
        self._return_df.loc[mask, self._confidence_col] = signal.confidence

    def run(self) -> DataFrame:
        # ExecutionSystem.run() is an async routine so it must be run
        # with asyncio.run() to start the event loop
        asyncio.run(self.execution_system.run())
        return self._return_df
