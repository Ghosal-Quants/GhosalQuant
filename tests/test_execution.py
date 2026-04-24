import asyncio
from datetime import datetime

from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.brokerage.simulated import SimBroker
from ghosalquant.execution.execution_system import ExecutionSystem
from examples.example_strategy import ExampleStrategy
from ghosalquant.core.types import SignalType, Signal, Bar, Order
from ghosalquant.execution.sinks import ListSink
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.risk.rules.rule_max_open_positions import MaxOpenPositions
from ghosalquant.risk.types import OrderDraft, RiskContext


# Helper function
def _bar(timestamp: str, **kwargs) -> Bar:
    return Bar(time_stamp=datetime.fromisoformat(timestamp), **kwargs)


def _order(timestamp: str, **kwargs) -> Order:
    return Order(time_stamp=datetime.fromisoformat(timestamp), **kwargs)


class RecordingSimBroker(SimBroker):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.submitted_orders: list[Order] = []

    def submit(self, order: Order) -> str:
        self.submitted_orders.append(order)
        return super().submit(order)


class CaptureContextRule:
    def __init__(self) -> None:
        self.contexts: list[RiskContext] = []

    def apply(self, _draft: OrderDraft, context: RiskContext) -> bool:
        self.contexts.append(RiskContext(
            open_positions=context.open_positions,
            open_qty_by_symbol=dict(context.open_qty_by_symbol),
            current_drawdown=context.current_drawdown,
            peak_equity=context.peak_equity,
            price_history=(
                {symbol: list(prices) for symbol, prices in context.price_history.items()}
                if context.price_history is not None else None
            ),
        ))
        return True


class BuyOnBarsStrategy:
    def __init__(self, buy_bar_indexes: list[int], quantity: float) -> None:
        self._buy_bar_indexes = set(buy_bar_indexes)
        self._quantity = quantity
        self._bar_index = -1

    def on_start(self) -> None:
        return None

    def on_end(self) -> None:
        return None

    def on_market_data(self, bar: Bar) -> Signal | None:
        self._bar_index += 1
        if self._bar_index not in self._buy_bar_indexes:
            return None

        return Signal(
            time_stamp=bar.time_stamp,
            symbol=bar.symbol,
            signal_type=SignalType.BUY,
            ref_price=bar.close,
            confidence=1.0,
            quantity=self._quantity,
        )


def test_chain_sinks():
    bars: list[Bar] = [
        _bar("2025-11-15 09:00:00", symbol="TEST", open=100.0, high=101.1, low=100.0, close=100.8, volume=1000),
        _bar("2025-11-15 09:01:00", symbol="TEST", open=100.8, high=101.1, low=100.0, close=99.1, volume=1000)
    ]

    list_sink: ListSink = ListSink()

    execution: ExecutionSystem = ExecutionSystem(
        data_src=ListDataSource(bars=bars),
        strategy=ExampleStrategy(),
        signal_destination=ListSink(next_sink=ListSink(next_sink=ListSink(next_sink=list_sink))),
    )

    # Apply strategy
    asyncio.run(execution.run())

    assert len(list_sink.get_signals()) == 1
    assert list_sink.get_signals()[0].signal_type is SignalType.BUY


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


def test_sim_broker_waits_for_next_bar_and_uses_bar_open():
    broker = SimBroker(
        spread_bps=0.0,
        slippage_mean_bps=0.0,
        slippage_std_bps=0.0,
        impact_bps_at_full_participation=0.0,
        max_fill_pct_of_bar_volume=1.0,
        seed=7,
    )
    entry_bar = _bar(
        "2025-11-15 09:00:00",
        symbol="TEST",
        open=100.0,
        high=101.0,
        low=99.5,
        close=100.5,
        volume=1000,
    )
    fill_bar = _bar(
        "2025-11-15 09:05:00",
        symbol="TEST",
        open=101.25,
        high=102.0,
        low=101.0,
        close=101.5,
        volume=1000,
    )

    broker.on_bar(entry_bar)
    broker.submit(
        _order(
            "2025-11-15 09:00:00",
            symbol="TEST",
            side=SignalType.BUY,
            quantity=10.0,
            ref_price=entry_bar.close,
        )
    )

    assert broker.get_fills() == []

    broker.on_bar(fill_bar)
    fills = broker.get_fills()

    assert len(fills) == 1
    assert fills[0].quantity == 10.0
    assert fills[0].price == fill_bar.open
    assert fills[0].time_stamp == fill_bar.time_stamp


def test_sim_broker_caps_fill_size_by_bar_volume():
    broker = SimBroker(
        spread_bps=0.0,
        slippage_mean_bps=0.0,
        slippage_std_bps=0.0,
        impact_bps_at_full_participation=0.0,
        partial_fill_probability=0.0,
        max_fill_pct_of_bar_volume=0.10,
        seed=11,
    )
    entry_bar = _bar(
        "2025-11-15 09:00:00",
        symbol="TEST",
        open=100.0,
        high=100.5,
        low=99.5,
        close=100.0,
        volume=1000,
    )
    first_fill_bar = _bar(
        "2025-11-15 09:05:00",
        symbol="TEST",
        open=100.5,
        high=101.0,
        low=100.0,
        close=100.8,
        volume=1000,
    )
    second_fill_bar = _bar(
        "2025-11-15 09:10:00",
        symbol="TEST",
        open=100.8,
        high=101.2,
        low=100.6,
        close=101.0,
        volume=1000,
    )

    broker.on_bar(entry_bar)
    broker.submit(
        _order(
            "2025-11-15 09:00:00",
            symbol="TEST",
            side=SignalType.BUY,
            quantity=150.0,
            ref_price=entry_bar.close,
        )
    )

    broker.on_bar(first_fill_bar)
    first_fills = broker.get_fills()
    assert len(first_fills) == 1
    assert first_fills[0].quantity == 100.0
    assert first_fills[0].price == first_fill_bar.open

    broker.on_bar(second_fill_bar)
    second_fills = broker.get_fills()
    assert len(second_fills) == 1
    assert second_fills[0].quantity == 50.0
    assert second_fills[0].price == second_fill_bar.open


def test_execution_builds_risk_context_from_portfolio_and_price_history():
    bars: list[Bar] = [
        _bar("2025-11-15 09:00:00", symbol="TEST", open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
        _bar("2025-11-15 09:05:00", symbol="TEST", open=100.0, high=100.0, low=100.0, close=100.0, volume=1000),
        _bar("2025-11-15 09:10:00", symbol="TEST", open=80.0, high=80.0, low=80.0, close=80.0, volume=1000),
    ]
    capture_rule = CaptureContextRule()
    broker = RecordingSimBroker(
        spread_bps=0.0,
        slippage_mean_bps=0.0,
        slippage_std_bps=0.0,
        impact_bps_at_full_participation=0.0,
        max_fill_pct_of_bar_volume=1.0,
        seed=17,
    )
    execution = ExecutionSystem(
        data_src=ListDataSource(bars=bars),
        strategy=BuyOnBarsStrategy(buy_bar_indexes=[0, 2], quantity=50.0),
        risk_manager=RiskManager(rules=[
            capture_rule,
            MaxOpenPositions(max_open_positions=1),
        ]),
        portfolio=BasicPortfolio(init_capital=10_000.0),
        broker=broker,
    )

    asyncio.run(execution.run())

    assert len(capture_rule.contexts) == 2
    assert len(broker.submitted_orders) == 1

    first_context = capture_rule.contexts[0]
    assert first_context.open_positions == 0
    assert first_context.open_qty_by_symbol == {}
    assert first_context.peak_equity == 10_000.0
    assert first_context.current_drawdown == 0.0
    assert first_context.price_history == {"TEST": [100.0]}

    second_context = capture_rule.contexts[1]
    assert second_context.open_positions == 1
    assert second_context.open_qty_by_symbol == {"TEST": 50.0}
    assert second_context.peak_equity == 10_000.0
    assert second_context.price_history == {"TEST": [100.0, 100.0, 80.0]}
    assert second_context.current_drawdown is not None
    assert abs(second_context.current_drawdown - (-0.1)) < 1e-12
