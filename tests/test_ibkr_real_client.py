from datetime import timedelta

import pytest

from ghosalquant.brokerage.ibkr import IBKRBroker
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import Liquidity, Order, PublicTrade, SignalType
from ibkr_venue import T0, Quote, ScriptedStrategy, Venue, at_second, book, run

BUY = SignalType.BUY
CANCEL_REMAINDER = {metadata_keys.CANCEL_REMAINDER_AFTER_FIRST_FILL: True}


def _order(
    quantity: float = 10.0, limit: float | None = 99.5, liquidity: Liquidity | None = None, **metadata
) -> Order:
    return Order(
        time_stamp=T0, symbol="AAPL", side=BUY, quantity=quantity, ref_price=99.5,
        limit_price=limit, liquidity=liquidity, metadata=metadata,
    )


@pytest.fixture
def venue() -> Venue:
    return Venue(IBKRBroker())


# ----- What goes out to IB ----- #

@pytest.mark.parametrize(
    "order, expected_type, expected_tif",
    [
        (_order(liquidity=Liquidity.TAKER), "LMT", "IOC"),
        (_order(limit=None, liquidity=Liquidity.TAKER), "MKT", "IOC"),
        (_order(**{metadata_keys.LIQUIDITY: "taker"}), "LMT", "IOC"),
        (_order(liquidity=Liquidity.MAKER), "LMT", ""),
        (_order(), "LMT", ""),
        (_order(limit=None), "MKT", ""),
    ],
    ids=["taker_limit", "taker_market", "taker_in_metadata", "maker", "undeclared_limit", "undeclared_market"],
)
def test_only_a_taker_goes_out_immediate_or_cancel(venue, order, expected_type, expected_tif):
    venue.broker.submit(order)

    assert venue.placed[-1].orderType == expected_type
    assert venue.placed[-1].tif == expected_tif


def test_what_an_ioc_could_not_fill_is_gone_once_ib_says_so(venue):
    order_id = venue.broker.submit(_order(liquidity=Liquidity.TAKER))

    assert venue.broker.open_order_views("AAPL") == ()
    assert order_id == "101"


# ----- A remainder is cancelled after the first fill ----- #

def test_a_first_fill_cancels_the_remainder_when_the_order_asks(venue):
    order_id = int(venue.broker.submit(_order(**CANCEL_REMAINDER)))

    venue.execute(order_id, 3.0)

    assert venue.cancels == [order_id]
    assert venue.trade(order_id).orderStatus.status == "PendingCancel"
    assert [fill.quantity for fill in venue.broker.get_fills()] == [3.0]


def test_fills_arriving_before_ib_confirms_are_kept_and_draw_no_second_cancel(venue):
    order_id = int(venue.broker.submit(_order(**CANCEL_REMAINDER)))

    venue.execute(order_id, 3.0)
    venue.execute(order_id, 2.0)
    venue.confirm_cancel(order_id)

    assert venue.cancels == [order_id]
    assert sum(fill.quantity for fill in venue.broker.get_fills()) == 5.0
    assert venue.broker.open_order_views("AAPL") == ()


def test_a_partial_fill_leaves_the_remainder_unless_the_order_asks(venue):
    order_id = int(venue.broker.submit(_order()))

    venue.execute(order_id, 3.0)

    assert venue.cancels == []
    assert venue.broker.open_order_views("AAPL")[0].remaining_quantity == 7.0


def test_a_complete_fill_sends_no_cancel(venue):
    order_id = int(venue.broker.submit(_order(**CANCEL_REMAINDER)))

    venue.execute(order_id, 10.0)

    assert venue.cancels == []


def test_a_restart_cancels_the_remainder_of_an_order_that_part_filled_while_down(tmp_path):
    state = tmp_path / "ibkr.json"
    first = IBKRBroker(state_path=state)
    venue = Venue(first)
    order_id = int(first.submit(_order(**CANCEL_REMAINDER)))
    # After a reconnect IB can report the fill on the status before any execution is matched up.
    venue.status(order_id, "Submitted", 3.0, 7.0)

    restarted = IBKRBroker(state_path=state)
    restarted.ib = venue.ib
    restarted.on_market_event(book(1))

    assert venue.cancels == [order_id]


# ----- Cancels ----- #

def test_a_repeat_cancel_is_not_sent_while_the_first_is_pending(venue):
    order_id = int(venue.broker.submit(_order()))

    assert venue.broker.cancel_open_orders("AAPL") == 1
    assert venue.broker.cancel_open_orders("AAPL") == 0

    assert venue.cancels == [order_id]
    assert venue.trade(order_id).orderStatus.status == "PendingCancel"


def test_an_ib_error_on_a_pending_cancel_draws_no_second_cancel(venue):
    order_id = int(venue.broker.submit(_order()))
    venue.broker.cancel_open_orders("AAPL")

    venue.ib.wrapper.error(order_id, 161, "Cancel attempted when order is not in a cancellable state", "")

    assert venue.broker.cancel_open_orders("AAPL") == 0
    assert venue.cancels == [order_id]


def test_cancelling_by_slot_leaves_other_slots_and_orders_with_none(venue):
    entry = int(venue.broker.submit(_order(limit=99.0, **{metadata_keys.ORDER_SLOT: "entry"})))
    venue.broker.submit(_order(limit=98.0, **{metadata_keys.ORDER_SLOT: "reserve"}))
    venue.broker.submit(_order(limit=97.0))

    assert venue.broker.cancel_open_orders("AAPL", BUY, ["entry"]) == 1
    assert venue.cancels == [entry]


# ----- Event age ----- #

def test_an_order_is_cancelled_after_its_events_and_only_its_symbol_counts():
    venue = Venue(IBKRBroker(max_order_age_events=2))
    order_id = int(venue.broker.submit(_order()))

    venue.broker.on_market_event(book(1))
    venue.broker.on_market_event(book(2, symbol="MSFT"))
    assert venue.cancels == []

    venue.broker.on_market_event(book(3))
    assert venue.cancels == [order_id]


def test_a_negative_event_age_is_refused():
    with pytest.raises(ValueError):
        IBKRBroker(max_order_age_events=0)


# ----- What comes back from IB ----- #

@pytest.mark.parametrize(
    "last_liquidity, expected",
    [(1, Liquidity.MAKER), (2, Liquidity.TAKER), (3, Liquidity.TAKER), (0, None)],
    ids=["added", "removed", "routed_out", "unknown"],
)
def test_a_fill_reports_the_liquidity_ib_says_it_had(venue, last_liquidity, expected):
    order_id = int(venue.broker.submit(_order()))

    venue.execute(order_id, 1.0, last_liquidity=last_liquidity)

    assert venue.broker.get_fills()[0].liquidity is expected


def test_a_resting_order_reports_the_liquidity_it_declared(venue):
    venue.broker.submit(_order())

    assert venue.broker.open_order_views("AAPL")[0].liquidity is Liquidity.MAKER


# ----- One strategy, one stream, either broker ----- #

def _simulated(**settings) -> OrderBookSimBroker:
    return OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=3, model_queue_position=False, **settings)


def _both_ways(plan, events, *, broker_settings=None, execute=None):
    """Run the plan through a simulated broker and through IB answering at once. Returns both results."""
    settings = broker_settings or {}

    simulated = ScriptedStrategy(plan)
    simulated_portfolio = run(simulated, _simulated(**settings), events)

    live = ScriptedStrategy(plan)
    venue = Venue(IBKRBroker(**settings), confirms_cancels=True)

    def before(event) -> None:
        for order_id, shares in (execute or {}).get(at_second(event), ()):
            venue.execute(order_id, shares)

    live_portfolio = run(live, venue.broker, events, before=before)
    return (simulated, simulated_portfolio), (live, live_portfolio)


def _assert_the_same(both) -> None:
    (simulated, simulated_portfolio), (live, live_portfolio) = both
    assert live.resting == simulated.resting
    assert live.accepted == simulated.accepted
    assert live.expired == simulated.expired
    assert live_portfolio.open_qty_by_symbol == simulated_portfolio.open_qty_by_symbol


def test_a_taker_that_cannot_fill_never_rests_in_either():
    both = _both_ways(
        {0: [Quote(BUY, 99.0, liquidity=Liquidity.TAKER)]},
        [book(second) for second in range(3)],
    )

    _assert_the_same(both)
    assert both[0][0].resting[1] == []


@pytest.mark.parametrize(
    "metadata, resting_after",
    [(CANCEL_REMAINDER, []), ({}, [("BUY", 100.0, 7.0)])],
    ids=["asks_to_cancel", "does_not_ask"],
)
def test_a_first_fill_ends_the_order_the_same_way_in_either(metadata, resting_after):
    events = [
        book(0),
        PublicTrade(time_stamp=T0 + timedelta(seconds=1), symbol="AAPL", price=100.0, quantity=3.0),
        book(2),
    ]

    both = _both_ways({0: [Quote(BUY, 100.0, metadata=metadata)]}, events, execute={1: [(101, 3.0)]})

    _assert_the_same(both)
    assert both[0][0].resting[2] == resting_after
    assert both[0][1].open_qty_by_symbol["AAPL"] == 3.0


def test_event_age_ends_an_order_at_the_same_event_in_either():
    events = [book(0), book(1, symbol="MSFT"), book(2), book(3, symbol="MSFT"), book(4), book(5)]

    both = _both_ways({0: [Quote(BUY, 99.0)]}, events, broker_settings={"max_order_age_events": 2})

    _assert_the_same(both)
    assert both[0][0].resting == {0: [], 2: [("BUY", 99.0, 10.0)], 4: [], 5: []}
    assert both[0][0].expired == []


def test_a_time_to_live_ends_an_order_at_the_same_moment_in_either():
    both = _both_ways(
        {0: [Quote(BUY, 99.0, metadata={metadata_keys.MAX_ORDER_AGE_SECONDS: 2.0})]},
        [book(0)] + [book(second, symbol="MSFT") for second in (1, 2, 3)] + [book(4)],
    )

    _assert_the_same(both)
    assert both[0][0].expired == [("BUY", 99.0, T0 + timedelta(seconds=2))]
    assert both[0][0].resting[4] == []


def test_cancelling_by_slot_counts_the_same_in_either():
    results = []
    for broker in (_simulated(), Venue(IBKRBroker(), confirms_cancels=True).broker):
        broker.submit(_order(limit=99.0, **{metadata_keys.ORDER_SLOT: "entry"}))
        broker.submit(_order(limit=98.0, **{metadata_keys.ORDER_SLOT: "reserve"}))
        results.append((
            broker.cancel_open_orders("AAPL", SignalType.SELL),
            broker.cancel_open_orders("AAPL", BUY, ["entry"]),
            broker.cancel_open_orders("AAPL", BUY, ["entry"]),
            [view.limit_price for view in broker.open_order_views("AAPL")],
        ))

    assert results[0] == results[1] == (0, 1, 0, [98.0])
