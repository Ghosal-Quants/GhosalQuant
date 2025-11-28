import asyncio
from datetime import datetime

from data_layer.list_datasource import ListDataSource
from execution.execution_system import ExecutionSystem
from example_strategy import ExampleStrategy
from core.types import SignalType, Signal, Bar
from execution.sinks import ListSink

# Helper function
def _bar(timestamp: str, **kwargs) -> Bar:
    return Bar(time_stamp=datetime.fromisoformat(timestamp), **kwargs)


def test_chain_sinks():
    bars: list[Bar] = [
        _bar("2025-11-15 09:00:00", symbol="TEST", open=100.0, high=101.1, low=100.0, close=100.8, volume=1000)
    ]

    list_sink: ListSink = ListSink()

    execution: ExecutionSystem = ExecutionSystem(
        data_src=ListDataSource(bars=bars),
        strategy=ExampleStrategy(),
        signal_destination=list_sink,
        ignore_noop_signals=False
    )

    # Apply strategy
    asyncio.run(execution.run())

    assert len(list_sink.get_signals()) == 1
    assert list_sink.get_signals()[0].signal_type == SignalType.NOOP


def test_execute_strategy():
    # define Bars
    bars: list[Bar] = [
        _bar("2025-11-15 09:00:00", symbol="TEST", open=100.0, high=101.1, low=100.0, close=100.8, volume=1000),
        _bar("2025-11-15 09:05:00", symbol="TEST", open=100.8, high=101.5, low=100.7, close=101.2, volume=1000),
        _bar("2025-11-15 09:10:00", symbol="TEST", open=101.2, high=101.4, low=100.8, close=100.9, volume=1000),
        _bar("2025-11-15 09:15:00", symbol="TEST", open=100.9, high=101.6, low=100.8, close=101.5, volume=1000),
        _bar("2025-11-15 09:20:00", symbol="TEST", open=101.5, high=102.4, low=101.3, close=102.0, volume=1000),
        _bar("2025-11-15 09:25:00", symbol="TEST", open=102.0, high=102.9, low=101.9, close=102.5, volume=1000),
        _bar("2025-11-15 09:30:00", symbol="TEST", open=102.5, high=103.1, low=102.3, close=102.8, volume=1000),
    ]

    signals: list[Signal] = []

    # instantiate execution system
    list_sink: ListSink = ListSink(signals=signals)

    execution: ExecutionSystem = ExecutionSystem(
        data_src=ListDataSource(bars=bars),
        strategy=ExampleStrategy(),
        signal_destination=list_sink,
        ignore_noop_signals=False
    )

    # Assert the buy and sells exist in the updated df

    # Manually calculated expected signals (based on Close price comparison):
    # Index 0: NaN (No previous close) -> 0
    # Index 1: 101.20 > 100.80 -> +1 (Buy)
    # Index 2: 100.90 < 101.20 -> -1 (Sell)
    # Index 3: 101.50 > 100.90 -> +1 (Buy)
    # Index 4: 102.00 > 101.50 -> +1 (Buy)
    # Index 5: 102.50 > 102.00 -> +1 (Buy)
    # Index 6: 102.80 > 102.50 -> +1 (Buy)

    # Apply strategy
    asyncio.run(execution.run())

    expected_signal_types = [
        SignalType.NOOP.value,
        SignalType.SELL.value,
        SignalType.BUY.value,
        SignalType.SELL.value,
        SignalType.SELL.value,
        SignalType.SELL.value,
        SignalType.SELL.value
    ]

    assert len(list_sink.get_signals()) == len(expected_signal_types)

    # Check if the generated signal matches the expected signal types
    i: int = 0
    for signal in list_sink.get_signals():
        assert signal.signal_type.value == expected_signal_types[i]
        i += 1
