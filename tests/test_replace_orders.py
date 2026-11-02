import pytest

from ghosalquant.brokerage.broker_base import Broker
from ghosalquant.brokerage.ibkr import IBKRBroker
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import SignalType
from ghosalquant.risk.risk_manager import RiskManager
from ibkr_venue import Quote, ScriptedStrategy, Venue, at_second, book, run

BUY, SELL = SignalType.BUY, SignalType.SELL
REPLACE_ENTRIES = {metadata_keys.REPLACE_ENTRY_ORDERS: True}
REPLACE_EXITS = {metadata_keys.REPLACE_EXIT_ORDERS: True}


def _simulated() -> OrderBookSimBroker:
    return OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=3)


def _both_ways(plan, last_second: int = 3):
    events = [book(second) for second in range(last_second + 1)]
    simulated = ScriptedStrategy(plan)
    run(simulated, _simulated(), events)
    live = ScriptedStrategy(plan)
    run(live, Venue(IBKRBroker(), confirms_cancels=True).broker, events)
    assert live.resting == simulated.resting
    assert live.accepted == simulated.accepted
    return simulated


# ----- The same in simulation and at IB when cancels confirm at once ----- #

def test_a_replacing_order_cancels_its_own_side_first():
    strategy = _both_ways({
        0: [Quote(BUY, 99.0), Quote(BUY, 98.0), Quote(SELL, 101.0)],
        1: [Quote(BUY, 99.5, metadata=REPLACE_ENTRIES)],
    })

    assert strategy.resting[2] == [("BUY", 99.5, 10.0), ("SELL", 101.0, 10.0)]


def test_a_replacing_exit_leaves_the_entries():
    strategy = _both_ways({
        0: [Quote(BUY, 99.0), Quote(SELL, 101.0), Quote(SELL, 102.0)],
        1: [Quote(SELL, 100.5, metadata=REPLACE_EXITS)],
    })

    assert strategy.resting[2] == [("BUY", 99.0, 10.0), ("SELL", 100.5, 10.0)]


def test_a_ladder_keeps_every_rung_it_sends_in_one_event():
    strategy = _both_ways({
        0: [Quote(BUY, 99.0)],
        1: [Quote(BUY, 98.5, metadata=REPLACE_ENTRIES), Quote(BUY, 98.0, metadata=REPLACE_ENTRIES)],
    })

    assert strategy.resting[2] == [("BUY", 98.0, 10.0), ("BUY", 98.5, 10.0)]


def test_an_order_without_the_flag_replaces_nothing():
    strategy = _both_ways({0: [Quote(BUY, 99.0)], 1: [Quote(BUY, 98.0)]})

    assert strategy.resting[2] == [("BUY", 98.0, 10.0), ("BUY", 99.0, 10.0)]


# ----- Nothing is cancelled for an order that will not go out ----- #

class _BlockAfterFirst:
    def __init__(self) -> None:
        self.seen = 0

    def apply(self, draft, context) -> bool:
        self.seen += 1
        return self.seen == 1


@pytest.mark.parametrize("make_broker", [_simulated, lambda: Venue(IBKRBroker(), confirms_cancels=True).broker],
                         ids=["simulated", "ibkr"])
def test_an_order_the_risk_rules_block_cancels_nothing(make_broker):
    strategy = ScriptedStrategy({0: [Quote(BUY, 99.0)], 1: [Quote(BUY, 99.5, metadata=REPLACE_ENTRIES)]})

    run(strategy, make_broker(), [book(0), book(1), book(2)], risk_manager=RiskManager(rules=[_BlockAfterFirst()]))

    assert strategy.resting[2] == [("BUY", 99.0, 10.0)]


def test_a_broker_that_cannot_report_orders_just_submits():
    class Blind(Broker):
        def __init__(self) -> None:
            self.submitted = []

        def submit(self, order):
            self.submitted.append(order)
            return "1"

        def get_fills(self):
            return []

    broker = Blind()
    strategy = ScriptedStrategy({0: [Quote(BUY, 99.0, metadata=REPLACE_ENTRIES)]})

    run(strategy, broker, [book(0)])

    assert len(broker.submitted) == 1


# ----- At IB a cancel takes time, and both orders must not work at once ----- #

def _at_ib(plan, seconds, *, script=None):
    """IB leaves each cancel pending; ``script`` maps a second to what IB does just before that event."""
    venue = Venue(IBKRBroker())
    strategy = ScriptedStrategy(plan)

    def before(event) -> None:
        action = (script or {}).get(at_second(event))
        if action is not None:
            action(venue)

    run(strategy, venue.broker, [book(second) for second in seconds], before=before)
    return venue, strategy


def _prices(venue) -> list[float]:
    return [order.lmtPrice for order in venue.placed]


def test_a_replacement_waits_until_ib_confirms_the_cancel():
    venue, strategy = _at_ib(
        {0: [Quote(BUY, 99.0)], 1: [Quote(BUY, 99.5, metadata=REPLACE_ENTRIES)]},
        range(5),
        script={3: lambda ib: ib.confirm_cancel(101)},
    )

    assert venue.cancels == [101]
    assert _prices(venue) == [99.0, 99.5]
    assert strategy.resting[2] == [("BUY", 99.0, 10.0)]
    assert strategy.resting[3] == [("BUY", 99.5, 10.0)]
    assert strategy.accepted == [("BUY", 99.0), ("BUY", 99.5)]


def test_a_held_ladder_goes_out_whole():
    venue, _ = _at_ib(
        {0: [Quote(BUY, 99.0)], 1: [Quote(BUY, 98.5, metadata=REPLACE_ENTRIES), Quote(BUY, 98.0, metadata=REPLACE_ENTRIES)]},
        range(4),
        script={2: lambda ib: ib.confirm_cancel(101)},
    )

    assert _prices(venue) == [99.0, 98.5, 98.0]


def test_a_replaced_order_that_fills_drops_the_replacement():
    def fill_then_confirm(ib: Venue) -> None:
        ib.execute(101, 4.0, 99.0)
        ib.confirm_cancel(101)

    venue, _ = _at_ib(
        {0: [Quote(BUY, 99.0)], 1: [Quote(BUY, 99.5, metadata=REPLACE_ENTRIES)]},
        range(5),
        script={2: fill_then_confirm},
    )

    assert _prices(venue) == [99.0]


def test_a_newer_replacement_supersedes_a_held_one():
    venue, _ = _at_ib(
        {
            0: [Quote(BUY, 99.0)],
            1: [Quote(BUY, 99.5, metadata=REPLACE_ENTRIES)],
            2: [Quote(BUY, 99.7, metadata=REPLACE_ENTRIES)],
        },
        range(5),
        script={3: lambda ib: ib.confirm_cancel(101)},
    )

    assert venue.cancels == [101]
    assert _prices(venue) == [99.0, 99.7]


def test_a_replacement_left_waiting_too_long_is_dropped():
    venue, _ = _at_ib(
        {0: [Quote(BUY, 99.0)], 1: [Quote(BUY, 99.5, metadata=REPLACE_ENTRIES)]},
        [0, 1, 2, 31, 32],
        script={32: lambda ib: ib.confirm_cancel(101)},
    )

    assert venue.cancels == [101]
    assert _prices(venue) == [99.0]
