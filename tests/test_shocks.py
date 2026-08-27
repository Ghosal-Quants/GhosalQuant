import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core.types import Bar, BookLevel, OrderBook, PublicTrade, Signal, SignalType
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.data_layer.recording_datasource import RecordedDataSource, RecordingDataSource
from ghosalquant.data_layer.shocks import Shock, ShockedDataSource, shocked
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk.risk_manager import RiskManager

T0 = datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc)


def book(seconds: float, price: float, *, symbol: str = "AAPL", size: float = 500.0) -> OrderBook:
    return OrderBook(
        time_stamp=T0 + timedelta(seconds=seconds), symbol=symbol,
        bids=(BookLevel(price, size), BookLevel(round(price - 0.01, 4), size * 2)),
        asks=(BookLevel(round(price + 0.02, 4), size),),
        checksum="original",
    )


def drain(source) -> list:
    async def collect():
        return [event async for event in source]

    return asyncio.run(collect())


# ----- One event at a time ----- #

def test_a_gap_moves_every_price_in_a_book_and_leaves_it_well_formed():
    gapped = shocked(book(0, 100.0), Shock(price_fraction=-0.05))

    assert gapped.bids[0].price == pytest.approx(95.0)
    assert gapped.asks[0].price == pytest.approx(95.019)
    assert [level.price for level in gapped.bids] == sorted(
        (level.price for level in gapped.bids), reverse=True
    )
    # Derived fields are recomputed, not carried over.
    assert gapped.mid_price == pytest.approx(95.0095)


def test_a_shocked_book_no_longer_claims_the_original_checksum():
    assert book(0, 100.0).checksum == "original"
    assert shocked(book(0, 100.0), Shock(price_fraction=-0.05)).checksum is None


def test_depth_can_be_thinned_without_moving_prices():
    thin = shocked(book(0, 100.0, size=500.0), Shock(depth_fraction=0.25))

    assert thin.bids[0].price == pytest.approx(100.0)
    assert thin.bids[0].quantity == pytest.approx(125.0)
    assert thin.ask_quantity == pytest.approx(125.0)


def test_prints_and_bars_are_shocked_too():
    trade = PublicTrade(time_stamp=T0, symbol="AAPL", price=100.0, quantity=40.0)
    bar = Bar(time_stamp=T0, symbol="AAPL", open=100.0, high=101.0, low=99.0, close=100.5, volume=10.0)
    gap = Shock(price_fraction=-0.05, depth_fraction=0.5)

    shocked_trade, shocked_bar = shocked(trade, gap), shocked(bar, gap)

    assert (shocked_trade.price, shocked_trade.quantity) == (pytest.approx(95.0), pytest.approx(20.0))
    assert shocked_bar.close == pytest.approx(95.475)
    assert shocked_bar.low == pytest.approx(94.05)
    assert shocked_bar.volume == pytest.approx(5.0)


def test_an_offset_and_a_fraction_can_be_combined_and_composed():
    both = shocked(book(0, 100.0), Shock(price_fraction=-0.10, price_offset=-1.0))
    twice = shocked(book(0, 100.0), Shock(price_fraction=-0.10), Shock(price_offset=-1.0))

    assert both.bids[0].price == pytest.approx(89.0)
    assert twice.bids[0].price == pytest.approx(89.0)


def test_a_shock_to_a_price_that_is_not_a_market_is_refused():
    bar = Bar(time_stamp=T0, symbol="AAPL", open=100.0, high=101.0, low=99.0, close=100.0, volume=1.0)
    trade = PublicTrade(time_stamp=T0, symbol="AAPL", price=100.0, quantity=1.0)

    # Bar does not validate its own prices, so without a guard here it would go negative quietly.
    for event in (book(0, 100.0), trade, bar):
        with pytest.raises(ValueError):
            shocked(event, Shock(price_fraction=-1.5))

    with pytest.raises(ValueError):
        Shock(depth_fraction=-0.5)


# ----- Across a stream ----- #

def test_a_gap_starts_when_it_is_dated_and_holds():
    stream = [book(second, 100.0) for second in range(4)]
    gap = Shock(price_fraction=-0.05, at=T0 + timedelta(seconds=2))

    prices = [event.bids[0].price for event in drain(ShockedDataSource(ListDataSource(stream), gap))]

    assert prices == pytest.approx([100.0, 100.0, 95.0, 95.0])


def test_a_gap_can_be_confined_to_some_symbols():
    stream = [book(0, 100.0, symbol="AAPL"), book(0, 50.0, symbol="MSFT")]
    gap = Shock(price_fraction=-0.05, symbols=("AAPL",))

    aapl, msft = drain(ShockedDataSource(ListDataSource(stream), gap))

    assert aapl.bids[0].price == pytest.approx(95.0)
    assert msft.bids[0].price == pytest.approx(50.0)


def test_a_source_needs_something_to_shock():
    with pytest.raises(ValueError):
        ShockedDataSource(ListDataSource([]))


# ----- The reason it exists ----- #

class DipBuyer(OrderBookStrategy):
    """Lifts the offer once it is cheap enough, so a gap is what triggers it."""

    def __init__(self, below: float) -> None:
        super().__init__()
        self.below = below
        self.fills: list = []

    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_order_book(self, book, context=None):
        if book.best_ask is None or book.best_ask.price >= self.below or context.has_open_orders:
            return None
        return Signal(
            time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
            ref_price=book.best_ask.price, confidence=1.0, quantity=1.0,
            limit_price=book.best_ask.price,
        )

    def on_fill(self, fill) -> None:
        self.fills.append(fill)


def run(source) -> DipBuyer:
    strategy = DipBuyer(below=97.0)
    asyncio.run(ExecutionSystem(
        source, strategy,
        portfolio=BasicPortfolio(init_capital=10_000.0),
        risk_manager=RiskManager(),
        broker=OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=2),
    ).run())
    return strategy


def test_a_recorded_session_can_be_replayed_through_a_market_that_did_not_happen(tmp_path):
    stream = [book(second, 100.0) for second in range(6)]
    drain(RecordingDataSource(ListDataSource(stream), tmp_path))

    as_it_happened = run(RecordedDataSource(tmp_path))
    gapped = run(ShockedDataSource(RecordedDataSource(tmp_path), Shock(price_fraction=-0.05)))

    assert as_it_happened.fills == []
    assert gapped.fills
    assert gapped.fills[0].price == pytest.approx(95.019)
