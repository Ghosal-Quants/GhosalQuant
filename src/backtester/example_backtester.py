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
            volume_col: str = "Volume"
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

    async def handle_signal(self, signal: Signal) -> None:
        # Dummy implementation
        print(f"[BACKTESTER]: {signal}")

    def run(self) -> None:
        # ExecutionSystem.run() is an async routine so it must be run
        # with asyncio.run() to start the event loop
        asyncio.run(self.execution_system.run())
