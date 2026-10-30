import asyncio
from datetime import datetime, timedelta

import pytest

from ghosalquant.brokerage.open_orders import OpenOrderQueries
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import (
    BookLevel,
    Liquidity,
    Order,
    OrderBook,
    PublicTrade,
    Signal,
    SignalType,
)
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.data_layer.merge import merge_datasources
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk.risk_manager import RiskManager

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


def _trade(seconds: float, price: float, quantity: float, trade_id: str | None = None, symbol: str = "TEST") -> PublicTrade:
    return PublicTrade(time_stamp=_at(seconds), symbol=symbol, price=price, quantity=quantity, trade_id=trade_id)


def _order(seconds: float, side: SignalType, quantity: float, limit: float | None,
           liquidity: Liquidity | None = None, **metadata) -> Order:
    return Order(
        time_stamp=_at(seconds),
        symbol="TEST",
        side=side,
        quantity=quantity,
        ref_price=limit if limit is not None else 100.0,
        limit_price=limit,
        liquidity=liquidity,
        metadata=metadata or None,
    )


def _broker(**overrides) -> OrderBookSimBroker:
    settings = dict(maker_rebate_bps=0.0, taker_fee_bps=0.0)
    settings.update(overrides)
    return OrderBookSimBroker(**settings)


# ----- Construction ----- #

def test_fee_schedule_is_required_and_settings_validated():
    with pytest.raises(TypeError):
        OrderBookSimBroker()
    with pytest.raises(ValueError):
        _broker(public_trade_participation=0.0)
    with pytest.raises(ValueError):
        _broker(public_trade_participation=1.5)
    with pytest.raises(ValueError):
        _broker(lot_size=0.0)
    with pytest.raises(ValueError):
        _broker(max_order_age_events=0)
    with pytest.raises(ValueError):
        _broker(max_order_age_seconds=-1.0)
    assert isinstance(_broker(), OpenOrderQueries)


# ----- Maker orders rest and need a print ----- #

def test_maker_order_rests_behind_displayed_queue_and_needs_a_print():
    broker = _broker()
    broker.on_order_book(_book(0, [(100.0, 12)], [(100.5, 10)]))
    broker.submit(_order(0, SignalType.BUY, 5.0, 100.0, Liquidity.MAKER))

    view = broker.open_order_views("TEST")[0]
    assert view.queue_ahead_quantity == 12.0
    assert view.liquidity is Liquidity.MAKER

    # More books alone never fill a resting maker order.
    broker.on_order_book(_book(1, [(100.0, 12)], [(100.5, 10)]))
    assert broker.get_fills() == []

    # A print at the limit first eats the queue ahead.
    broker.on_public_trade(_trade(2, 100.0, 8.0, "t1"))
    assert broker.get_fills() == []
    assert broker.open_order_views("TEST")[0].queue_ahead_quantity == 4.0

    # The next print clears the rest of the queue and fills what is left of it.
    broker.on_public_trade(_trade(3, 100.0, 7.0, "t2"))
    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].quantity == 3.0
    assert fills[0].price == 100.0
    assert fills[0].liquidity is Liquidity.MAKER
    assert broker.open_order_views("TEST")[0].remaining_quantity == 2.0


def test_print_must_reach_the_limit():
    broker = _broker()
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 5.0, 100.0, Liquidity.MAKER))

    broker.on_public_trade(_trade(1, 100.5, 10.0, "a"))
    assert broker.get_fills() == []

    broker.on_public_trade(_trade(2, 99.5, 10.0, "b"))
    assert len(broker.get_fills()) == 1


def test_sell_side_prints_fill_resting_offers():
    broker = _broker()
    broker.on_order_book(_book(0, [(99.0, 5)], [(100.0, 4)]))
    broker.submit(_order(0, SignalType.SELL, 3.0, 100.0, Liquidity.MAKER))
    assert broker.open_order_views("TEST")[0].queue_ahead_quantity == 4.0

    broker.on_public_trade(_trade(1, 99.5, 10.0, "low"))
    assert broker.get_fills() == []

    broker.on_public_trade(_trade(2, 100.0, 4.0, "queue"))
    assert broker.get_fills() == []

    broker.on_public_trade(_trade(3, 100.5, 2.0, "fill"))
    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].side is SignalType.SELL
    assert fills[0].quantity == 2.0
    assert fills[0].price == 100.0


def test_participation_caps_how_much_of_a_print_can_fill_us():
    broker = _broker(public_trade_participation=0.25, model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 100.0, 100.0, Liquidity.MAKER))

    broker.on_public_trade(_trade(1, 100.0, 40.0, "p"))
    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].quantity == 10.0


def test_lot_size_floors_fills_to_whole_units():
    broker = _broker(public_trade_participation=0.25, lot_size=1.0, model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 100.0, 100.0, Liquidity.MAKER))

    broker.on_public_trade(_trade(1, 100.0, 7.0, "p"))  # 25% of 7 == 1.75 -> 1
    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].quantity == 1.0


def test_one_print_is_shared_by_price_then_time_priority():
    broker = _broker(model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(2, SignalType.BUY, 3.0, 100.0, Liquidity.MAKER))   # later, worse price
    broker.submit(_order(1, SignalType.BUY, 3.0, 100.2, Liquidity.MAKER))   # better price wins
    broker.submit(_order(0, SignalType.BUY, 3.0, 100.0, Liquidity.MAKER))   # same price, earlier

    broker.on_public_trade(_trade(3, 100.0, 7.0, "p"))
    fills = broker.get_fills()
    assert [(f.price, f.quantity) for f in fills] == [(100.2, 3.0), (100.0, 3.0), (100.0, 1.0)]


def test_the_queue_ahead_of_each_order_draws_on_the_same_print():
    broker = _broker()
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 3.0, 100.0, Liquidity.MAKER))
    broker.submit(_order(1, SignalType.BUY, 3.0, 100.0, Liquidity.MAKER))

    broker.on_public_trade(_trade(2, 100.0, 7.0, "p"))
    assert [(f.price, f.quantity) for f in broker.get_fills()] == [(100.0, 2.0)]
    assert [view.queue_ahead_quantity for view in broker.open_order_views("TEST")] == [0.0, 5.0]


def test_a_print_through_our_price_is_not_capped_by_participation():
    broker = _broker(public_trade_participation=0.25, model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 100.0, 100.5, Liquidity.MAKER))

    broker.on_public_trade(_trade(1, 100.0, 40.0, "p"))
    assert [(f.price, f.quantity) for f in broker.get_fills()] == [(100.5, 40.0)]


def test_duplicate_trade_ids_are_ignored():
    broker = _broker(model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 10.0, 100.0, Liquidity.MAKER))

    broker.on_public_trade(_trade(1, 100.0, 2.0, "dup"))
    broker.on_public_trade(_trade(1, 100.0, 2.0, "dup"))
    assert sum(f.quantity for f in broker.get_fills()) == 2.0


def test_a_duplicate_print_does_not_advance_the_queue():
    broker = _broker()
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 1.0, 100.0, Liquidity.MAKER))

    broker.on_public_trade(_trade(1, 100.0, 3.0, "dup"))
    broker.on_public_trade(_trade(1, 100.0, 3.0, "dup"))
    assert broker.open_order_views("TEST")[0].queue_ahead_quantity == 2.0


def test_prints_before_the_order_existed_do_not_fill_it():
    broker = _broker(model_queue_position=False)
    broker.on_order_book(_book(5, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(5, SignalType.BUY, 5.0, 100.0, Liquidity.MAKER))
    broker.on_public_trade(_trade(4, 100.0, 5.0, "old"))
    assert broker.get_fills() == []


def test_an_order_is_not_submitted_before_its_own_time():
    broker = _broker(model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(5, SignalType.BUY, 5.0, 100.0, Liquidity.MAKER))
    broker.on_public_trade(_trade(3, 100.0, 5.0, "earlier"))
    assert broker.get_fills() == []


# ----- Taker orders ----- #

def test_taker_sweeps_levels_and_never_rests():
    broker = _broker(taker_fee_bps=10.0)
    broker.on_order_book(_book(0, [(99.0, 5)], [(100.0, 2), (100.5, 3), (101.0, 10)]))
    broker.submit(_order(0, SignalType.BUY, 6.0, 100.5, Liquidity.TAKER))

    fills = broker.get_fills()
    assert [(f.price, f.quantity) for f in fills] == [(100.0, 2.0), (100.5, 3.0)]
    assert all(f.liquidity is Liquidity.TAKER for f in fills)
    assert broker.open_order_views("TEST") == ()

    expected_fee = (100.0 * 2.0 + 100.5 * 3.0) * 0.001
    assert sum(f.commission for f in fills) == pytest.approx(expected_fee)


def test_a_maker_order_that_crosses_on_arrival_takes_the_level_as_a_taker():
    broker = _broker(maker_rebate_bps=20.0, taker_fee_bps=50.0)
    broker.on_order_book(_book(0, [(99.0, 5)], [(100.0, 2)]))
    broker.submit(_order(0, SignalType.BUY, 3.0, 100.5, Liquidity.MAKER))

    fill, = broker.get_fills()
    assert (fill.price, fill.quantity, fill.liquidity) == (100.0, 2.0, Liquidity.TAKER)
    assert fill.commission == pytest.approx(100.0 * 2.0 * 0.005)
    assert broker.open_order_views("TEST")[0].remaining_quantity == 1.0


def test_taker_consumption_is_withheld_from_the_book_until_redisplayed():
    broker = _broker()
    broker.on_order_book(_book(0, [(99.0, 5)], [(100.0, 10)]))
    broker.submit(_order(0, SignalType.BUY, 4.0, 100.0, Liquidity.TAKER))
    assert sum(f.quantity for f in broker.get_fills()) == 4.0

    same = _book(1, [(99.0, 5)], [(100.0, 10)])
    adjusted = broker.on_order_book(same)
    assert adjusted.best_ask.quantity == 6.0
    assert same.best_ask.quantity == 10.0

    # Once the venue redisplays more size the withheld amount decays away.
    refreshed = broker.on_order_book(_book(2, [(99.0, 5)], [(100.0, 14)]))
    assert refreshed.best_ask.quantity == 14.0


def test_a_level_the_venue_requotes_is_no_longer_withheld():
    broker = _broker()
    broker.on_order_book(_book(0, [(99.0, 5)], [(100.0, 10)]))
    broker.submit(_order(0, SignalType.BUY, 4.0, 100.0, Liquidity.TAKER))
    broker.get_fills()

    # A venue model that knows the ask at 100.0 was requoted in full says so.
    broker.fill_model.redisplayed("TEST", "ask", 100.0)
    assert broker.on_order_book(_book(1, [(99.0, 5)], [(100.0, 6)])).best_ask.quantity == 6.0


def test_maker_order_crossed_by_the_book_fills_at_its_own_limit():
    broker = _broker()
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 2.0, 100.5, Liquidity.MAKER))
    assert broker.get_fills() == []

    broker.on_order_book(_book(1, [(100.0, 5)], [(100.2, 5)]))
    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].price == 100.5
    assert fills[0].liquidity is Liquidity.MAKER


def test_maker_rebate_is_credited_as_negative_commission():
    broker = _broker(maker_rebate_bps=20.0, model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 2.0, 100.0, Liquidity.MAKER))
    broker.on_public_trade(_trade(1, 100.0, 5.0, "p"))

    fill = broker.get_fills()[0]
    assert fill.commission == pytest.approx(-(100.0 * 2.0) * 0.002)


def test_orders_default_to_maker_and_honour_metadata_liquidity():
    broker = _broker()
    broker.on_order_book(_book(0, [(99.0, 1)], [(100.0, 10)]))

    broker.submit(_order(0, SignalType.BUY, 1.0, 99.0))
    assert broker.open_order_views("TEST")[0].liquidity is Liquidity.MAKER
    assert broker.get_fills() == []

    broker.submit(_order(0, SignalType.BUY, 1.0, 100.0, **{metadata_keys.LIQUIDITY: "taker"}))
    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].liquidity is Liquidity.TAKER


# ----- Lifecycle ----- #

def test_cancel_remainder_after_first_fill():
    broker = _broker(model_queue_position=False)
    broker.on_order_book(_book(0, [(100.0, 0)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 10.0, 100.0, Liquidity.MAKER,
                         **{metadata_keys.CANCEL_REMAINDER_AFTER_FIRST_FILL: True}))
    broker.on_public_trade(_trade(1, 100.0, 3.0, "p"))

    assert sum(f.quantity for f in broker.get_fills()) == 3.0
    assert broker.open_order_views("TEST") == ()


def test_wall_clock_expiry_and_next_expiry_reporting():
    broker = _broker(max_order_age_seconds=30.0)
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 1.0, 100.0, Liquidity.MAKER))

    assert broker.next_order_expiry_at() == _at(30)
    assert broker.order_symbols_expiring_by(_at(29)) == set()
    assert broker.order_symbols_expiring_by(_at(30)) == {"TEST"}
    assert broker.expire_orders(now=_at(29)) == []
    assert len(broker.expire_orders(now=_at(30))) == 1
    assert broker.open_order_views() == ()
    assert broker.next_order_expiry_at() is None


def test_per_order_ttl_metadata_overrides_the_broker_default():
    broker = _broker(max_order_age_seconds=1000.0)
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 1.0, 100.0, Liquidity.MAKER,
                         **{metadata_keys.MAX_ORDER_AGE_SECONDS: 5.0}))
    assert broker.next_order_expiry_at() == _at(5)


def test_event_age_expiry():
    broker = _broker(max_order_age_events=2)
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 1.0, 100.0, Liquidity.MAKER))

    broker.on_order_book(_book(1, [(100.0, 5)], [(101.0, 5)]))
    assert len(broker.open_order_views()) == 1
    broker.on_order_book(_book(2, [(100.0, 5)], [(101.0, 5)]))
    assert broker.open_order_views() == ()


def test_cancel_by_symbol_side_and_slot():
    broker = _broker()
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 1.0, 99.0, Liquidity.MAKER, **{metadata_keys.ORDER_SLOT: "entry"}))
    broker.submit(_order(0, SignalType.BUY, 1.0, 98.0, Liquidity.MAKER, **{metadata_keys.ORDER_SLOT: "reserve"}))
    broker.submit(_order(0, SignalType.SELL, 1.0, 102.0, Liquidity.MAKER))

    assert broker.cancel_open_orders("OTHER") == 0
    assert broker.cancel_open_orders("TEST", SignalType.BUY, ["entry"]) == 1
    assert broker.cancel_open_orders("TEST", SignalType.SELL) == 1
    assert len(broker.open_order_views("TEST")) == 1
    assert broker.cancel_open_orders("TEST") == 1
    assert broker.open_order_views() == ()


def test_observe_unchanged_book_does_not_match_orders():
    broker = _broker(max_order_age_events=1)
    broker.on_order_book(_book(0, [(100.0, 5)], [(101.0, 5)]))
    broker.submit(_order(0, SignalType.BUY, 1.0, 100.0, Liquidity.MAKER))
    broker.observe_unchanged_book(_book(1, [(100.0, 5)], [(101.0, 5)]))
    assert len(broker.open_order_views()) == 1


def test_an_unchanged_book_still_moves_the_broker_clock():
    broker = _broker()
    broker.on_order_book(_book(0, [(99.0, 5)], [(100.0, 5)]))
    broker.observe_unchanged_book(_book(9, [(99.0, 5)], [(100.0, 5)]))
    broker.submit(_order(9, SignalType.BUY, 1.0, 100.0, Liquidity.TAKER))
    assert [fill.time_stamp for fill in broker.get_fills()] == [_at(9)]


# ----- Through the ExecutionSystem ----- #

class RestingBidStrategy(OrderBookStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.fills = []

    def on_start(self) -> None: ...
    def on_end(self) -> None: ...

    def on_order_book(self, book, context):
        if context.has_open_orders or context.position_quantity > 0.0 or not book.is_two_sided():
            return None
        price = book.best_bid.price
        return Signal(book.time_stamp, book.symbol, SignalType.BUY, price, quantity=3.0,
                      limit_price=price, liquidity=Liquidity.MAKER)

    def on_fill(self, fill) -> None:
        self.fills.append(fill)


def test_end_to_end_through_execution_system():
    books = [_book(second, [(100.0, 2)], [(100.5, 5)]) for second in range(4)]
    prints = [_trade(1.5, 100.0, 4.0, "p1"), _trade(2.5, 100.0, 4.0, "p2")]
    strategy = RestingBidStrategy()
    portfolio = BasicPortfolio(init_capital=10_000.0)
    broker = _broker(maker_rebate_bps=10.0)

    execution = ExecutionSystem(
        data_src=merge_datasources(ListDataSource(books), ListDataSource(prints)),
        strategy=strategy,
        risk_manager=RiskManager(rules=[]),
        portfolio=portfolio,
        broker=broker,
    )
    asyncio.run(execution.run())

    # p1 clears the 2 displayed ahead and fills 2; p2 fills the last 1.
    assert [f.quantity for f in strategy.fills] == [2.0, 1.0]
    assert portfolio.open_qty_by_symbol == {"TEST": 3.0}
    assert portfolio.average_entry_price("TEST") == pytest.approx(100.0)
    assert all(f.commission < 0 for f in strategy.fills)


def test_a_taker_arriving_between_quotes_waits_for_the_next_one():
    """
    A strategy trading one symbol off another's event submits before that symbol next quotes.

    The order used to be dropped on the spot: no fill, no rejection, nothing the strategy could
    see. It waits for the next quote instead, and crosses that rather than the one it was priced
    against, which is what arriving late actually costs.
    """
    broker = OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0)
    broker.on_market_event(_book(0, [(99.0, 5.0)], [(100.0, 5.0)]))
    broker.on_market_event(_book(1, [], [], symbol="OTHER"))

    broker.submit(_order(1, SignalType.BUY, 5.0, 100.5, Liquidity.TAKER))
    assert broker.get_fills() == []

    broker.on_market_event(_book(2, [(99.5, 5.0)], [(100.5, 5.0)]))
    fills = broker.get_fills()

    assert [(f.quantity, f.price) for f in fills] == [(5.0, 100.5)]
    assert broker.open_order_views() == ()


def test_a_taker_that_misses_its_chance_does_not_linger():
    """One quote is the whole of a taker's life: what it cannot cross there is cancelled."""
    broker = OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0)
    broker.on_market_event(_book(0, [(99.0, 5.0)], [(100.0, 5.0)]))
    broker.on_market_event(_book(1, [], [], symbol="OTHER"))

    broker.submit(_order(1, SignalType.BUY, 5.0, 100.0, Liquidity.TAKER))
    broker.on_market_event(_book(2, [(99.5, 5.0)], [(101.0, 5.0)]))  # moved away

    assert broker.get_fills() == []
    assert broker.open_order_views() == ()
