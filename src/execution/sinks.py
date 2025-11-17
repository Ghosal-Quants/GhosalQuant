from typing import Protocol
from core.types import Signal, SignalType
import pandas as pd

class SignalSink(Protocol):
    async def on_signal(self, signal: Signal) -> None: ...

class PrintSink(SignalSink):
    async def on_signal(self, signal: Signal) -> None:
        print(f"[PrintSink]: {signal}")

class BacktesterSink(SignalSink):
    def __init__(self, backtester) -> None:
        self.backtester = backtester

    async def on_signal(self, signal: Signal) -> None:
        await self.backtester.handle_signal(signal)

class DataFrameSink(SignalSink):
    def __init__(
            self,
            dataframe: pd.DataFrame | None = None,
            *,
            timestamp_col: str,
            signal_col: str,
            confidence_col: str
    ) -> None:
        if dataframe is None:
            dataframe = pd.DataFrame(
                columns=[timestamp_col, signal_col, confidence_col]
            )

        self._dataframe = dataframe
        self._timestamp_col = timestamp_col
        self._signal_col = signal_col
        self._confidence_col = confidence_col

    async def on_signal(self, signal: Signal) -> None:
        # TODO: Handle cases where user wants to accumulate new rows for signals, instead of updating existing rows

        if signal.signal_type not in (SignalType.BUY, SignalType.SELL):
            return

        # Record signals into new column in the duplicate DataFrame
        if self._timestamp_col is not None:
            mask = self._dataframe[self._timestamp_col] == signal.time_stamp
        else:
            mask = self._dataframe.index == signal.time_stamp

        if not mask.any():
            print("[WARN]: No row found for signal timestamp:", signal.time_stamp)

        self._dataframe.loc[mask, self._signal_col] = signal.signal_type.value
        self._dataframe.loc[mask, self._confidence_col] = signal.confidence

    def get_dataframe(self) -> pd.DataFrame:
        return self._dataframe

class BrokerSink(SignalSink):
    async def on_signal(self, signal: Signal) -> None:
        raise NotImplementedError("BrokerSink is not implemented yet.")

class RiskManagerSink(SignalSink):
    async def on_signal(self, signal: Signal) -> None:
        raise NotImplementedError("RiskManager is not implemented yet.")
