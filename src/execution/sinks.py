from __future__ import annotations
from typing import Protocol
from core.types import Signal, SignalType
import pandas as pd
from dataclasses import replace


class SignalSink(Protocol):
    def __init__(self, next_sink: SignalSink | None = None):
        self._next_sink = next_sink

    @staticmethod
    async def pass_to_next(next_sink: SignalSink | None, signal: Signal) -> None:
        if next_sink is not None:
            await next_sink.on_signal(signal)

    async def on_signal(self, signal: Signal) -> None: ...


class PrintSink(SignalSink):
    def __init__(self, next_sink: SignalSink | None = None):
        super().__init__(next_sink)

    async def on_signal(self, signal: Signal) -> None:
        print(f"[PrintSink]: {signal}")
        await self.pass_to_next(self._next_sink, signal)


class BacktesterSink(SignalSink):
    def __init__(self, backtester, next_sink: SignalSink | None = None) -> None:
        super().__init__(next_sink)
        self.backtester = backtester

    async def on_signal(self, signal: Signal) -> None:
        await self.backtester.handle_signal(signal)
        await self.pass_to_next(self._next_sink, signal)


class DataFrameSink(SignalSink):
    def __init__(
            self,
            next_sink: SignalSink | None = None,
            dataframe: pd.DataFrame | None = None,
            *,
            timestamp_col: str,
            signal_col: str,
            confidence_col: str,
            quantity_col: str
    ) -> None:
        super().__init__(next_sink)

        self._dataframe = dataframe
        self._timestamp_col = timestamp_col
        self._signal_col = signal_col
        self._confidence_col = confidence_col
        self._quantity_col = quantity_col

        if self._timestamp_col is None:
            self._use_index_for_timestamp = True
        else:
            if self._timestamp_col in self._dataframe.columns:
                self._use_index_for_timestamp = False
            else:
                raise ValueError(f"Timestamp column '{self._timestamp_col}' not found in DataFrame columns.")

        if dataframe is None:
            self._dataframe = pd.DataFrame(
                columns=[timestamp_col, signal_col, confidence_col, quantity_col]
            )
        else:
            required_cols = [
                self._signal_col,
                self._confidence_col,
                self._quantity_col,
            ]

            for col in required_cols:
                if col not in self._dataframe.columns:
                    self._dataframe[col] = None


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
        self._dataframe.loc[mask, self._quantity_col] = signal.quantity

        meta = getattr(signal, "metadata", None) or None

        if meta is None:
            return

        for key, value in meta.items():
            if key not in self._dataframe.columns:
                self._dataframe[key] = None
            self._dataframe.loc[mask, key] = value

        await self.pass_to_next(self._next_sink, signal)

    def get_dataframe(self) -> pd.DataFrame:
        return self._dataframe


class BrokerSink(SignalSink):
    def __init__(self, next_sink: SignalSink = None):
        super().__init__(next_sink)
    async def on_signal(self, signal: Signal) -> None:
        raise NotImplementedError("BrokerSink is not implemented yet.")


class RiskManagerSink(SignalSink):
    def __init__(
            self, 
            next_sink:SignalSink, 
            confidence_threshold: float = 0.5, 
            max_qty: int = 10,
            min_qty: int = 0
    ) -> None:
        super().__init__(next_sink)
        self._confidence_threshold = confidence_threshold
        self._max_qty = max_qty
        self._min_qty = min_qty
    

    async def on_signal(self, signal: Signal) -> None:
        if signal.signal_type is SignalType.NOOP:
            return
        
        if signal.confidence >= self._confidence_threshold:
            qty = self._max_qty
        else:
            qty = self._min_qty

        adjusted_signal = replace(signal, quantity=qty)

        await self._next_sink.on_signal(adjusted_signal)
        await self.pass_to_next(self._next_sink, signal)

class ListSink(SignalSink):
    def __init__(self, next_sink: SignalSink | None = None, signals: list[Signal] | None = None):
        super().__init__(next_sink)
        if signals is None:
            signals = []

        self._signals: list[Signal] = signals

    async def on_signal(self, signal: Signal) -> None:
        self._signals.append(signal)
        await self.pass_to_next(self._next_sink, signal)

    def get_signals(self) -> list[Signal]:
        return self._signals