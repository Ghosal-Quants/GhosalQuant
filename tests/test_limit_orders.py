from datetime import datetime

import pytest
from ib_insync import LimitOrder, MarketOrder

from ghosalquant.brokerage.ibkr import IBKRBroker
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.brokerage.simulated import SimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import Bar, BookLevel, Liquidity, Order, OrderBook, Signal, SignalType
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.risk.types import OrderDraft, RiskContext


T0 = datetime(2025, 11, 15, 9, 30, 0)


def _bar(minute: int, open_: float) -> Bar:
    return Bar(time_stamp=T0.replace(minute=30 + minute), symbol="TEST", open=open_, high=open_ + 1.0, low=open_ - 1.0, close=open_, volume=1000.0)


def _at(second: int) -> datetime:
    return T0.replace(second=second)


def _book(second: int, bid: tuple[float, float], ask: tuple[float, float]) -> OrderBook:
    return OrderBook(time_stamp=T0.replace(second=second), symbol="TEST", bids=(BookLevel(*bid),), asks=(BookLevel(*ask),))


def _sim_broker() -> SimBroker:
    return SimBroker(spread_bps=0.0, slippage_mean_bps=0.0, slippage_std_bps=0.0, impact_bps_at_full_participation=0.0,
                     max_fill_pct_of_bar_volume=1.0, seed=5)


# ----- Signal / Order fields ----- #

def test_signal_limit_and_liquidity_fields():
    signal = Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, limit_price=99.5, liquidity="maker")
    assert signal.limit_price == 99.5
    assert signal.liquidity is Liquidity.MAKER

    plain = Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0)
    assert plain.limit_price is None and plain.liquidity is None

    with pytest.raises(ValueError):
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, limit_price=-1.0)
    with pytest.raises(TypeError):
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, liquidity=3)
    with pytest.raises(ValueError):
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, liquidity="passive")


# ----- RiskManager threading ----- #

def test_risk_manager_threads_limit_price_and_liquidity_to_the_order():
    manager = RiskManager(rules=[])
    order = manager.evaluate(
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, quantity=2.0,
               limit_price=99.0, liquidity=Liquidity.MAKER),
        RiskContext(),
    )
    assert order is not None
    assert order.limit_price == 99.0
    assert order.liquidity is Liquidity.MAKER
    assert order.ref_price == 100.0
    assert order.metadata == {metadata_keys.CONFIDENCE: 1.0}


def test_risk_manager_honours_metadata_limit_price_from_older_signals():
    manager = RiskManager(rules=[])
    order = manager.evaluate(
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.SELL, ref_price=100.0, quantity=1.0,
               metadata={metadata_keys.LIMIT_PRICE: 101.0}),
        RiskContext(),
    )
    assert order is not None
    assert order.limit_price == 101.0
    assert order.declared_limit_price == 101.0


def test_risk_rules_may_tighten_the_limit():
    class ShaveOneTick:
        def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
            if draft.limit_price is not None and draft.side is SignalType.BUY:
                draft.limit_price = round(draft.limit_price - 0.01, 2)
            return True

    manager = RiskManager(rules=[ShaveOneTick()])
    order = manager.evaluate(
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, quantity=1.0, limit_price=99.50),
        RiskContext(),
    )
    assert order is not None and order.limit_price == pytest.approx(99.49)


def test_market_signals_stay_market_orders():
    order = RiskManager(rules=[]).evaluate(
        Signal(time_stamp=T0, symbol="TEST", signal_type=SignalType.BUY, ref_price=100.0, quantity=1.0), RiskContext()
    )
    assert order is not None and order.limit_price is None and not order.is_limit


# ----- SimBroker honours limits ----- #

def test_sim_broker_bar_fill_rests_until_the_limit_is_reached():
    broker = _sim_broker()
    broker.on_bar(_bar(0, 100.0))
    broker.submit(Order(time_stamp=T0, symbol="TEST", side=SignalType.BUY, quantity=10.0, ref_price=100.0, limit_price=100.0))

    broker.on_bar(_bar(1, 101.0))
    assert broker.get_fills() == []
    assert broker.open_order_views("TEST")[0].limit_price == 100.0

    broker.on_bar(_bar(2, 99.5))
    fills = broker.get_fills()
    assert len(fills) == 1 and fills[0].price == 99.5 and fills[0].quantity == 10.0
    assert fills[0].liquidity is None

    broker.submit(Order(time_stamp=T0, symbol="TEST", side=SignalType.SELL, quantity=10.0, ref_price=99.5, limit_price=100.5))
    broker.on_bar(_bar(3, 100.0))
    assert broker.get_fills() == []
    broker.on_bar(_bar(4, 100.6))
    fills = broker.get_fills()
    assert len(fills) == 1 and fills[0].side is SignalType.SELL and fills[0].price == 100.6


def test_sim_broker_market_orders_still_fill_on_the_next_bar():
    broker = _sim_broker()
    broker.on_bar(_bar(0, 100.0))
    broker.submit(Order(time_stamp=T0, symbol="TEST", side=SignalType.BUY, quantity=1.0, ref_price=100.0))
    broker.on_bar(_bar(1, 104.0))
    fills = broker.get_fills()
    assert len(fills) == 1 and fills[0].price == 104.0



def test_book_broker_uses_the_limit_field_and_reports_taker_liquidity():
    broker = OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=5)

    # A taker whose limit the book does not reach fills nothing and does not rest.
    broker.on_order_book(_book(0, (100.0, 10), (100.5, 10)))
    broker.submit(Order(time_stamp=T0, symbol="TEST", side=SignalType.BUY, quantity=4.0,
                        ref_price=100.5, limit_price=100.4, liquidity=Liquidity.TAKER))
    assert broker.get_fills() == []
    assert broker.open_order_views("TEST") == ()

    # Once the ask reaches the limit it sweeps immediately, at the level rather than the limit.
    broker.on_order_book(_book(1, (100.0, 10), (100.3, 10)))
    broker.submit(Order(time_stamp=_at(1), symbol="TEST", side=SignalType.BUY, quantity=4.0,
                        ref_price=100.5, limit_price=100.4, liquidity=Liquidity.TAKER))
    fills = broker.get_fills()
    assert len(fills) == 1 and fills[0].price == 100.3
    assert fills[0].liquidity is Liquidity.TAKER

# ----- IBKRBroker sends limit orders ----- #

class _FakeTrade:
    def __init__(self, order) -> None:
        self.order = order
        self.order.orderId = 42


class _FakeIB:
    def __init__(self) -> None:
        self.placed: list[tuple[object, object]] = []

    def isConnected(self) -> bool:
        return True

    def placeOrder(self, contract, order):
        self.placed.append((contract, order))
        return _FakeTrade(order)


def test_ibkr_broker_submits_limit_orders_when_a_limit_is_set():
    broker = IBKRBroker()
    broker.ib = _FakeIB()

    assert broker.submit(Order(time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=3.0, ref_price=190.0)) == "42"
    assert broker.submit(Order(time_stamp=T0, symbol="AAPL", side=SignalType.SELL, quantity=3.0, ref_price=190.0, limit_price=191.25)) == "42"

    (_, market), (_, limit) = broker.ib.placed
    assert isinstance(market, MarketOrder) and market.action == "BUY" and market.totalQuantity == 3.0
    assert isinstance(limit, LimitOrder) and limit.action == "SELL" and limit.lmtPrice == 191.25
