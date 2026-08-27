import asyncio
from datetime import datetime, timedelta

import pytest

from ghosalquant.brokerage.open_orders import OpenOrderQueries
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.brokerage.simulated import SimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import (
    Bar,
    BookLevel,
    Fill,
    Liquidity,
    Order,
    OrderBook,
    PublicTrade,
    Signal,
    SignalType,
)
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.data_layer.merge import merge_datasources
from ghosalquant.execution.context import StrategyContext
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy, Strategy, as_signal_list
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.portfolio.marks import MarkPolicy, mark_from_book, mark_from_trade
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.state.event_store import EventStore
from ghosalquant.risk.types import OrderDraft, RiskContext


# ----- Helpers ----- #

T0 = datetime(2025, 11, 15, 9, 30, 0)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _book(seconds: float, bids, asks, symbol: str = "TEST") -> OrderBook:
    return OrderBook(
        time_stamp=_at(seconds),
        symbol=symbol,
        bids=tuple(BookLevel(p, q) for p, q in bids),
        asks=tuple(BookLevel(p, q) for p, q in asks),
    )


def _trade(seconds: float, price: float, quantity: float = 1.0, symbol: str = "TEST") -> PublicTrade:
    return PublicTrade(time_stamp=_at(seconds), symbol=symbol, price=price, quantity=quantity)


def _bar(seconds: float, close: float, symbol: str = "TEST") -> Bar:
    return Bar(time_stamp=_at(seconds), symbol=symbol, open=close, high=close, low=close, close=close, volume=1000.0)


def _sim_broker(**overrides) -> OrderBookSimBroker:
    settings = dict(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=3)
    settings.update(overrides)
    return OrderBookSimBroker(**settings)


def _bar_broker(**overrides) -> SimBroker:
    settings = dict(
        spread_bps=0.0,
        slippage_mean_bps=0.0,
        slippage_std_bps=0.0,
        impact_bps_at_full_participation=0.0,
        max_fill_pct_of_bar_volume=1.0,
        seed=3,
    )
    settings.update(overrides)
    return SimBroker(**settings)


class PassThroughRule:
    """Risk rule that keeps the draft and records every context it sees."""

    def __init__(self) -> None:
        self.contexts: list[RiskContext] = []

    def apply(self, _draft: OrderDraft, context: RiskContext) -> bool:
        self.contexts.append(context)
        return True


class TouchStrategy(OrderBookStrategy):
    """
    Flat with nothing resting: rest a bid at the best bid.
    Long with no resting offer: rest the position at the best ask.
    Records everything the loop hands it.
    """

    def __init__(self, quantity: float = 5.0) -> None:
        super().__init__()
        self.quantity = quantity
        self.books: list[OrderBook] = []
        self.contexts: list[StrategyContext] = []
        self.trades: list[PublicTrade] = []
        self.fills: list[Fill] = []
        self.accepted: list[tuple[Order, str | None]] = []
        self.started = False
        self.ended = False

    def on_start(self) -> None:
        self.started = True

    def on_end(self) -> None:
        self.ended = True

    def on_order_book(self, book: OrderBook, context: StrategyContext):
        self.books.append(book)
        self.contexts.append(context)
        if context.position_quantity == 0.0 and not context.has_open_orders and book.best_bid is not None:
            return Signal(
                time_stamp=book.time_stamp,
                symbol=book.symbol,
                signal_type=SignalType.BUY,
                ref_price=book.best_bid.price,
                quantity=self.quantity,
                metadata={metadata_keys.LIMIT_PRICE: book.best_bid.price},
            )
        if context.position_quantity > 0.0 and context.open_sell_quantity == 0.0 and book.best_ask is not None:
            return [
                Signal(
                    time_stamp=book.time_stamp,
                    symbol=book.symbol,
                    signal_type=SignalType.SELL,
                    ref_price=book.best_ask.price,
                    quantity=context.position_quantity,
                    metadata={metadata_keys.LIMIT_PRICE: book.best_ask.price},
                )
            ]
        return None

    def on_public_trade(self, trade: PublicTrade, context: StrategyContext):
        self.trades.append(trade)
        return None

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

    def on_order_accepted(self, order: Order, broker_order_id: str | None) -> None:
        self.accepted.append((order, broker_order_id))


class BarOnlyStrategy(Strategy):
    """A plain bar strategy: must be unaffected by books and prints in the stream."""

    def __init__(self) -> None:
        super().__init__()
        self.bars: list[Bar] = []

    def on_start(self) -> None:
        return None

    def on_end(self) -> None:
        return None

    def on_market_data(self, bar: Bar):
        self.bars.append(bar)
        return None


class RecordingPortfolio(BasicPortfolio):
    """BasicPortfolio that records every mark() the loop makes."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.marks: list[tuple[datetime, str, float]] = []

    def mark(self, symbol: str, price: float, time_stamp: datetime) -> None:
        self.marks.append((time_stamp, symbol, float(price)))
        super().mark(symbol, price, time_stamp)


def _run(execution: ExecutionSystem) -> None:
    asyncio.run(execution.run())


# ----- Hook plumbing ----- #

def test_as_signal_list_normalises_hook_results():
    signal = Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=1.0)
    assert as_signal_list(None) == []
    assert as_signal_list(signal) == [signal]
    assert as_signal_list([signal, signal]) == [signal, signal]
    assert as_signal_list(iter((signal,))) == [signal]
    with pytest.raises(TypeError):
        as_signal_list(["not a signal"])


def test_order_book_strategy_ignores_bars_and_requires_on_order_book():
    with pytest.raises(TypeError):
        class Incomplete(OrderBookStrategy):  # noqa: F811 - deliberately abstract
            def on_start(self): ...
            def on_end(self): ...
        Incomplete()

    strategy = TouchStrategy()
    assert strategy.on_market_data(_bar(0, 100.0)) is None


def test_sim_broker_implements_open_order_queries():
    broker = _sim_broker()
    assert isinstance(broker, OpenOrderQueries)

    broker.on_order_book(_book(0, [(100.0, 10)], [(100.5, 10)]))
    order_id = broker.submit(
        Order(time_stamp=_at(0), symbol="TEST", side=SignalType.BUY, quantity=4.0, ref_price=99.0,
              metadata={metadata_keys.LIMIT_PRICE: 99.0})
    )
    views = broker.open_order_views("TEST")
    assert len(views) == 1
    assert views[0].broker_order_id == order_id
    assert views[0].remaining_quantity == 4.0
    assert views[0].limit_price == 99.0
    assert broker.open_order_views("OTHER") == ()

    assert broker.cancel_open_orders("TEST", SignalType.SELL) == 0
    assert broker.cancel_open_orders("TEST") == 1
    assert broker.open_order_views() == ()


# ----- Broker behaviour through the loop ----- #



def test_bar_broker_ignores_books_and_prints():
    broker = _bar_broker()
    broker.on_public_trade(_trade(0, 100.0))
    broker.submit(Order(time_stamp=_at(0), symbol="TEST", side=SignalType.BUY, quantity=1.0, ref_price=100.0))
    broker.on_public_trade(_trade(1, 99.0))
    assert broker.get_fills() == []
    assert broker.open_order_views("TEST")[0].submitted_at == _at(0)


# ----- Marks ----- #

def test_mark_policies():
    two_sided = _book(0, [(100.0, 1)], [(101.0, 1)])
    bid_only = _book(0, [(100.0, 1)], [])

    assert mark_from_book(two_sided, previous_mark=None, position_quantity=0.0) == 100.5
    assert mark_from_book(bid_only, previous_mark=99.0, position_quantity=5.0) == 99.0
    assert mark_from_book(bid_only, previous_mark=None, position_quantity=5.0) is None
    assert mark_from_book(two_sided, previous_mark=None, position_quantity=5.0, policy=MarkPolicy.CONSERVATIVE) == 100.0
    assert mark_from_book(two_sided, previous_mark=None, position_quantity=-5.0, policy=MarkPolicy.CONSERVATIVE) == 101.0
    assert mark_from_book(two_sided, previous_mark=None, position_quantity=0.0, policy=MarkPolicy.CONSERVATIVE) == 100.5

    print_ = _trade(0, 102.0)
    assert mark_from_trade(print_, previous_mark=100.0, position_quantity=5.0) == 102.0
    assert mark_from_trade(print_, previous_mark=100.0, position_quantity=5.0, policy=MarkPolicy.CONSERVATIVE) == 100.0
    assert mark_from_trade(_trade(0, 98.0), previous_mark=100.0, position_quantity=5.0, policy=MarkPolicy.CONSERVATIVE) == 98.0
    assert mark_from_trade(print_, previous_mark=None, position_quantity=5.0, policy=MarkPolicy.CONSERVATIVE) == 102.0


def test_basic_portfolio_mark_does_not_snapshot_but_update_equity_does():
    portfolio = BasicPortfolio(init_capital=1_000.0)
    portfolio.mark("TEST", 10.0, T0)
    assert portfolio.last_prices == {"TEST": 10.0}
    assert list(portfolio.equity_curve) == []
    assert portfolio.average_entry_price("TEST") is None

    portfolio.update_equity(_bar(1, 11.0))
    assert len(list(portfolio.equity_curve)) == 1


# ----- Merge ----- #

def test_merge_orders_by_time_then_trade_book_bar():
    bar = _bar(0, 100.0)
    book = _book(0, [(100.0, 1)], [(100.5, 1)])
    trade = _trade(0, 100.2)
    later_book = _book(1, [(100.1, 1)], [(100.6, 1)])
    earlier_trade = _trade(-1, 99.0)

    merged = merge_datasources(
        ListDataSource([bar, later_book]),
        ListDataSource([book]),
        ListDataSource([earlier_trade, trade]),
    )

    async def collect():
        return [event async for event in merged]

    events = asyncio.run(collect())
    assert events == [earlier_trade, trade, book, bar, later_book]


def test_merge_of_one_source_is_that_source():
    source = ListDataSource([_bar(0, 1.0)])
    assert merge_datasources(source) is source
    with pytest.raises(ValueError):
        merge_datasources()


def test_list_datasource_accepts_bars_keyword_for_compatibility():
    bars = [_bar(0, 1.0)]
    assert ListDataSource(bars=bars).events is bars
    with pytest.raises(ValueError):
        ListDataSource(bars, bars=bars)


# ----- The loop ----- #

def _touch_scenario():
    """
    t0 rest BUY 5 @ 100.0; t0.5 it is still queued; t1 a print at the bid fills it.
    t2 long, so rest the offer at 100.5; t2.5 it is queued; t3 a print through it fills it.
    t4 flat again, so a fresh bid rests.
    """
    books = [
        _book(0, [(100.0, 10)], [(100.5, 10)]),
        _book(0.5, [(100.0, 10)], [(100.5, 10)]),
        _book(2, [(100.0, 10)], [(100.5, 10)]),
        _book(2.5, [(100.0, 10)], [(100.5, 10)]),
        _book(4, [(100.2, 10)], [(100.6, 10)]),
    ]
    prints = [_trade(1, 100.0, 5.0), _trade(3, 100.7, 5.0)]
    return books, prints



def test_order_book_strategy_round_trip_through_execution_system():
    books, prints = _touch_scenario()
    strategy = TouchStrategy()
    rule = PassThroughRule()
    broker = _sim_broker(model_queue_position=False)
    portfolio = RecordingPortfolio(init_capital=10_000.0)

    execution = ExecutionSystem(
        data_src=merge_datasources(ListDataSource(books), ListDataSource(prints)),
        strategy=strategy,
        risk_manager=RiskManager(rules=[rule]),
        portfolio=portfolio,
        broker=broker,
    )
    _run(execution)

    assert strategy.started and strategy.ended
    assert len(strategy.books) == 5
    assert len(strategy.trades) == 2

    # Neither order crosses on submission; each waits for a print that reaches its price.
    assert [(f.side, f.quantity, f.price) for f in strategy.fills] == [
        (SignalType.BUY, 5.0, 100.0),
        (SignalType.SELL, 5.0, 100.5),
    ]
    assert all(f.liquidity is Liquidity.MAKER for f in strategy.fills)

    assert [order.side for order, _ in strategy.accepted] == [SignalType.BUY, SignalType.SELL, SignalType.BUY]
    assert all(broker_id and broker_id.startswith("OBS-") for _, broker_id in strategy.accepted)

    first, queued, after_buy, offered, flat = strategy.contexts
    assert first.position_quantity == 0.0 and not first.has_open_orders
    assert queued.open_buy_quantity == 5.0 and queued.position_quantity == 0.0
    assert queued.open_quantity_at(100.0, SignalType.BUY) == 5.0
    assert after_buy.position_quantity == 5.0 and after_buy.open_sell_quantity == 0.0
    assert after_buy.average_entry_price == pytest.approx(100.0)
    assert offered.open_sell_quantity == 5.0
    assert flat.position_quantity == 0.0

    assert len(rule.contexts) == 3
    assert rule.contexts[0].open_orders == ()
    assert rule.contexts[0].price_history is None

    assert portfolio.open_qty_by_symbol == {}
    assert broker.open_order_views("TEST")[0].side is SignalType.BUY



def test_risk_context_sees_resting_orders():
    class AlwaysBid(OrderBookStrategy):
        def on_start(self): ...
        def on_end(self): ...

        def on_order_book(self, book, context):
            return Signal(book.time_stamp, book.symbol, SignalType.BUY, book.best_bid.price,
                          quantity=1.0, limit_price=book.best_bid.price, liquidity=Liquidity.MAKER)

    rule = PassThroughRule()
    execution = ExecutionSystem(
        data_src=ListDataSource([_book(0, [(100.0, 10)], [(100.5, 10)]),
                                 _book(1, [(100.0, 10)], [(100.5, 10)])]),
        strategy=AlwaysBid(),
        risk_manager=RiskManager(rules=[rule]),
        portfolio=BasicPortfolio(init_capital=10_000.0),
        broker=_sim_broker(model_queue_position=False),
    )
    _run(execution)

    assert rule.contexts[0].open_orders == ()
    assert [view.side for view in rule.contexts[1].open_orders] == [SignalType.BUY]
    assert rule.contexts[1].open_orders[0].remaining_quantity == 1.0


def test_portfolio_is_marked_from_the_raw_book_not_the_brokers_adjusted_view():
    class ShiftingBroker(SimBroker):
        def on_order_book(self, book: OrderBook) -> OrderBook:
            super().on_order_book(book)
            return book.replace(
                bids=tuple(BookLevel(level.price - 5.0, level.quantity) for level in book.bids),
                asks=tuple(BookLevel(level.price - 5.0, level.quantity) for level in book.asks),
            )

    strategy = TouchStrategy()
    portfolio = BasicPortfolio(init_capital=10_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource([_book(0, [(100.0, 1)], [(101.0, 1)])]),
        strategy=strategy,
        risk_manager=RiskManager(rules=[PassThroughRule()]),
        portfolio=portfolio,
        broker=ShiftingBroker(seed=1),
    )
    _run(execution)

    assert strategy.books[0].mid_price == pytest.approx(95.5)
    assert portfolio.last_prices["TEST"] == pytest.approx(100.5)


def test_one_sided_book_keeps_previous_mark_and_conservative_policy_marks_at_bid():
    strategy = TouchStrategy()
    portfolio = BasicPortfolio(init_capital=10_000.0)
    execution = ExecutionSystem(
        # Rest a bid, let a print fill it, then hold: the mark tracks the bid, a one-sided book
        # leaves it alone, and a print above it never raises a long's mark.
        data_src=ListDataSource([
            _book(0, [(100.0, 10)], [(100.5, 10)]),
            _trade(1, 100.0, 5.0),
            _book(2, [(99.9, 10)], [(100.4, 10)]),
            _book(3, [], [(100.4, 10)]),
            _trade(4, 100.2, 1.0),
        ]),
        strategy=strategy,
        risk_manager=RiskManager(rules=[PassThroughRule()]),
        portfolio=portfolio,
        broker=_sim_broker(model_queue_position=False),
        mark_policy=MarkPolicy.CONSERVATIVE,
    )
    _run(execution)

    assert portfolio.open_qty_by_symbol == {"TEST": 5.0}
    assert portfolio.last_prices["TEST"] == pytest.approx(99.9)



def test_bar_strategy_is_unaffected_by_books_and_prints_in_the_stream():
    strategy = BarOnlyStrategy()
    portfolio = BasicPortfolio(init_capital=1_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource([
            _book(0, [(100.0, 1)], [(100.5, 1)]),
            _bar(1, 100.0),
            _trade(2, 100.2),
            _bar(3, 101.0),
        ]),
        strategy=strategy,
        risk_manager=RiskManager(rules=[PassThroughRule()]),
        portfolio=portfolio,
        broker=_bar_broker(),
    )
    _run(execution)

    assert [bar.close for bar in strategy.bars] == [100.0, 101.0]
    assert portfolio.last_prices["TEST"] == 101.0


def test_duck_typed_strategy_without_hooks_still_runs_on_books():
    class Minimal:
        def __init__(self): self.bars = 0
        def on_start(self): ...
        def on_end(self): ...
        def on_market_data(self, bar): self.bars += 1; return None

    strategy = Minimal()
    execution = ExecutionSystem(
        data_src=ListDataSource([_book(0, [(1.0, 1)], [(2.0, 1)]), _trade(1, 1.5), _bar(2, 1.5)]),
        strategy=strategy,
    )
    _run(execution)

    # The book and the print are skipped rather than fatal, and the bar still arrives.
    assert strategy.bars == 1


def test_equity_snapshots_are_bounded_by_snapshot_interval():
    books = [_book(second, [(100.0 + second * 0.01, 10)], [(100.5 + second * 0.01, 10)]) for second in range(31)]
    portfolio = BasicPortfolio(init_capital=1_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource(books),
        strategy=BarOnlyStrategy(),
        risk_manager=RiskManager(rules=[]),
        portfolio=portfolio,
        broker=_sim_broker(),
        snapshot_interval=timedelta(seconds=10),
    )
    _run(execution)

    points = list(portfolio.equity_curve)
    assert [point.time_stamp for point in points] == [_at(0), _at(10), _at(20), _at(30)]
    assert portfolio.last_prices["TEST"] == pytest.approx(100.55)


class BuyOnceStrategy(OrderBookStrategy):
    def __init__(self, quantity: float = 5.0) -> None:
        super().__init__()
        self.quantity = quantity
        self.done = False

    def on_start(self) -> None: ...
    def on_end(self) -> None: ...

    def on_order_book(self, book: OrderBook, context: StrategyContext):
        if self.done or book.best_ask is None:
            return None
        self.done = True
        return Signal(book.time_stamp, book.symbol, SignalType.BUY, book.best_ask.price,
                      quantity=self.quantity, limit_price=book.best_ask.price + 5.0)


def test_final_equity_point_reflects_the_last_marks():
    books = [_book(second, [(100.0 + second, 10)], [(100.5 + second, 10)]) for second in range(6)]
    strategy = BuyOnceStrategy()
    portfolio = BasicPortfolio(init_capital=10_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource(books),
        strategy=strategy,
        risk_manager=RiskManager(rules=[PassThroughRule()]),
        portfolio=portfolio,
        broker=_sim_broker(),
        snapshot_interval=timedelta(hours=1),
    )
    _run(execution)

    curve = list(portfolio.equity_curve)
    position = portfolio.open_qty_by_symbol.get("TEST", 0.0)
    expected = portfolio.cash_balance + position * portfolio.last_prices["TEST"]

    assert position > 0.0
    assert curve[-1].time_stamp == books[-1].time_stamp
    assert curve[-1].equity == pytest.approx(expected)
    assert portfolio.current_equity == pytest.approx(expected)


def test_flush_does_not_duplicate_the_last_bar_point():
    bars = [_bar(second, 100.0 + second) for second in range(4)]
    portfolio = BasicPortfolio(init_capital=1_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource(bars),
        strategy=BarOnlyStrategy(),
        risk_manager=RiskManager(rules=[]),
        portfolio=portfolio,
        broker=_bar_broker(),
    )
    _run(execution)

    curve = list(portfolio.equity_curve)
    assert len(curve) == len(bars)
    assert curve[-1].time_stamp == bars[-1].time_stamp


def test_bars_still_snapshot_every_bar_and_feed_price_history():
    bars = [_bar(second, 100.0 + second) for second in range(5)]
    rule = PassThroughRule()

    class BuyOnLast(Strategy):
        def on_start(self): ...
        def on_end(self): ...
        def on_market_data(self, bar):
            if bar.close == 104.0:
                return Signal(time_stamp=bar.time_stamp, symbol=bar.symbol, signal_type=SignalType.BUY,
                              ref_price=bar.close, quantity=1.0)
            return None

    portfolio = BasicPortfolio(init_capital=1_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource(bars),
        strategy=BuyOnLast(),
        risk_manager=RiskManager(rules=[rule]),
        portfolio=portfolio,
        broker=_bar_broker(),
        snapshot_interval=timedelta(hours=1),
    )
    _run(execution)

    assert len(list(portfolio.equity_curve)) == 5
    assert rule.contexts[0].price_history == {"TEST": [100.0, 101.0, 102.0, 103.0, 104.0]}


# ----- Time-to-live expiry runs off the clock ----- #

class QuoteOnce(OrderBookStrategy):
    """Rests one bid on the first book for its symbol, then only watches."""

    def __init__(self, symbol: str = "TEST") -> None:
        super().__init__()
        self.symbol = symbol
        self.quoted = False
        self.expired: list = []
        self.resting_seen: list = []

    def on_start(self) -> None:
        return None

    def on_end(self) -> None:
        return None

    def on_order_book(self, book: OrderBook, context: StrategyContext | None = None):
        if book.symbol != self.symbol or self.quoted or book.best_bid is None:
            return None
        self.quoted = True
        return Signal(
            time_stamp=book.time_stamp, symbol=self.symbol, signal_type=SignalType.BUY,
            ref_price=book.best_bid.price, confidence=1.0, quantity=1.0,
            limit_price=book.best_bid.price,
        )

    def on_order_expired(self, order: Order, broker_order_id: str, expired_at, context: StrategyContext):
        self.expired.append((order.symbol, broker_order_id, (expired_at - T0).total_seconds()))
        return None


def _run_stream(stream, strategy, broker) -> None:
    _run(ExecutionSystem(
        ListDataSource(stream), strategy,
        portfolio=BasicPortfolio(init_capital=100_000.0),
        risk_manager=RiskManager(), broker=broker,
    ))


def _quote_then_other_symbol(last_second: int) -> list:
    stream = [_book(0, [(100.0, 500.0)], [(100.02, 500.0)])]
    stream += [
        _book(second, [(50.0, 500.0)], [(50.02, 500.0)], symbol="OTHER")
        for second in range(1, last_second + 1)
    ]
    return stream


def test_a_resting_order_expires_on_the_clock_not_when_its_symbol_next_ticks():
    broker = _sim_broker(max_order_age_seconds=2.0)

    class Watcher(QuoteOnce):
        def on_order_book(self, book, context=None):
            elapsed = (book.time_stamp - T0).total_seconds()
            self.resting_seen.append((elapsed, len(broker.open_order_views("TEST"))))
            return super().on_order_book(book, context)

    strategy = Watcher()
    _run_stream(_quote_then_other_symbol(5), strategy, broker)

    # Resting at t=1, gone from t=2, even though TEST never ticks again.
    assert [seen for seen in strategy.resting_seen if seen[0] >= 1] == [
        (1.0, 1), (2.0, 0), (3.0, 0), (4.0, 0), (5.0, 0)
    ]
    assert strategy.expired == [("TEST", "OBS-1", 2.0)]


def test_an_order_inside_its_time_to_live_is_left_alone():
    broker = _sim_broker(max_order_age_seconds=30.0)
    strategy = QuoteOnce()

    _run_stream(_quote_then_other_symbol(5), strategy, broker)

    assert strategy.expired == []
    assert len(broker.open_order_views("TEST")) == 1


def test_a_strategy_can_requote_from_the_expiry_hook():
    broker = _sim_broker(max_order_age_seconds=2.0)

    class Requote(QuoteOnce):
        def on_order_expired(self, order, broker_order_id, expired_at, context):
            self.expired.append((order.symbol, broker_order_id, (expired_at - T0).total_seconds()))
            return Signal(
                time_stamp=expired_at, symbol=order.symbol, signal_type=SignalType.BUY,
                ref_price=order.limit_price, confidence=1.0, quantity=1.0,
                limit_price=order.limit_price,
            )

    strategy = Requote()
    _run_stream(_quote_then_other_symbol(3), strategy, broker)

    # The replacement rests without waiting for TEST to tick again, and carries a fresh clock.
    assert strategy.expired == [("TEST", "OBS-1", 2.0)]
    assert len(broker.open_order_views("TEST")) == 1


def test_a_stale_requote_cannot_stall_the_clock():
    broker = _sim_broker(max_order_age_seconds=2.0)

    class StaleRequote(QuoteOnce):
        def on_order_expired(self, order, broker_order_id, expired_at, context):
            self.expired.append((order.symbol, broker_order_id, (expired_at - T0).total_seconds()))
            # Timestamped with the dead order's clock, so the replacement is born expired.
            return Signal(
                time_stamp=order.time_stamp, symbol=order.symbol, signal_type=SignalType.BUY,
                ref_price=order.limit_price, confidence=1.0, quantity=1.0,
                limit_price=order.limit_price,
            )

    strategy = StaleRequote()
    _run_stream(_quote_then_other_symbol(3), strategy, broker)

    # The replacement is expired without being announced, so the strategy is not asked again.
    assert strategy.expired == [("TEST", "OBS-1", 2.0)]
    assert broker.open_order_views("TEST") == ()


def test_a_broker_that_expires_nothing_is_left_alone():
    class NoExpiry(SimBroker):
        def next_order_expiry_at(self):
            return None

    broker = NoExpiry(seed=3, max_order_age_seconds=2.0)
    strategy = QuoteOnce()

    _run_stream(_quote_then_other_symbol(4), strategy, broker)

    assert strategy.expired == []


# ----- The fill journal ----- #

class _FillOnce(OrderBookStrategy):
    """Buys once, so exactly one fill flows through the journal."""

    def __init__(self) -> None:
        super().__init__()
        self.quoted = False
        self.fills: list = []

    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_order_book(self, book, context=None):
        if self.quoted or book.best_ask is None:
            return None
        self.quoted = True
        return Signal(
            time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
            ref_price=book.best_ask.price, confidence=1.0, quantity=1.0,
            limit_price=book.best_ask.price, liquidity=Liquidity.TAKER,
        )

    def on_fill(self, fill) -> None:
        self.fills.append(fill)


def _journalled_run(journal, portfolio=None):
    strategy = _FillOnce()
    system = ExecutionSystem(
        ListDataSource([
            _book(0, [(99.0, 500.0)], [(100.0, 500.0)]),
            _book(1, [(99.0, 500.0)], [(100.0, 500.0)]),
        ]),
        strategy,
        portfolio=portfolio if portfolio is not None else BasicPortfolio(init_capital=10_000.0),
        risk_manager=RiskManager(),
        broker=_sim_broker(),
        journal=journal,
    )
    _run(system)
    return strategy, system.portfolio


def test_a_journalled_fill_is_recorded_and_applied(tmp_path):
    with EventStore(tmp_path / "journal.db") as journal:
        strategy, portfolio = _journalled_run(journal)

        assert strategy.fills, "the run should have produced a fill"
        assert [event.event_type for event in journal.events()] == ["fill"] * len(strategy.fills)
        assert portfolio.open_qty_by_symbol.get("TEST", 0.0) > 0.0


def test_a_fill_already_in_the_journal_is_not_applied_again(tmp_path):
    path = tmp_path / "journal.db"
    with EventStore(path) as journal:
        strategy, _ = _journalled_run(journal)
    applied = len(strategy.fills)

    # The same session again, against the journal it left behind.
    with EventStore(path) as reopened:
        repeat, portfolio = _journalled_run(reopened)

        assert applied > 0
        assert repeat.fills == []
        assert portfolio.open_qty_by_symbol.get("TEST", 0.0) == 0.0
        assert len(reopened.events()) == applied


def test_a_run_that_dies_before_committing_replays_the_fill(tmp_path, monkeypatch):
    path = tmp_path / "journal.db"
    journal = EventStore(path)
    # Killed between recording the fill and committing it, so SQLite discards the event with
    # the connection. Losing it here is right: the portfolio it was applied to is gone too.
    monkeypatch.setattr(EventStore, "commit", lambda _self: None)
    _journalled_run(journal)
    monkeypatch.undo()
    journal.connection.close()

    with EventStore(path) as reopened:
        assert reopened.events() == []
        strategy, portfolio = _journalled_run(reopened)

        # Nothing was lost: the fill is applied on the next run rather than skipped.
        assert strategy.fills
        assert portfolio.open_qty_by_symbol.get("TEST", 0.0) > 0.0


def test_without_a_journal_fills_are_applied_exactly_as_before(tmp_path):
    strategy, portfolio = _journalled_run(None)

    assert strategy.fills
    assert portfolio.open_qty_by_symbol.get("TEST", 0.0) > 0.0
