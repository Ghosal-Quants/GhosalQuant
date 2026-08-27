from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pytest

from ghosalquant.brokerage import ibkr
from ghosalquant.brokerage.ibkr import IBKRBroker
from ghosalquant.brokerage.open_orders import OpenOrderQueries
from ghosalquant.core.types import Order, SignalType

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


# ----- A stand-in for ib_insync, so none of this needs a gateway ----- #

@dataclass
class FakeContract:
    symbol: str


@dataclass
class FakeIBOrder:
    orderId: int
    action: str = "BUY"
    totalQuantity: float = 10.0
    lmtPrice: float = 0.0


@dataclass
class FakeLogEntry:
    time: datetime


@dataclass
class FakeIBTrade:
    contract: FakeContract
    order: FakeIBOrder
    _remaining: float = 10.0
    log: list = field(default_factory=lambda: [FakeLogEntry(T0)])

    def remaining(self) -> float:
        return self._remaining


@dataclass
class FakeExecution:
    execId: str
    orderId: int
    shares: float
    price: float
    side: str = "BOT"


@dataclass
class FakeCommission:
    commission: float | None


@dataclass
class FakeFill:
    contract: FakeContract
    execution: FakeExecution
    commissionReport: FakeCommission
    time: datetime


class FakeEvent:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class FakeIB:
    """Only the surface IBKRBroker actually touches."""

    def __init__(self) -> None:
        self.execDetailsEvent = FakeEvent()
        self.open_trades: list[FakeIBTrade] = []
        self.cancelled: list[int] = []
        self.placed: list[tuple] = []
        self._next_order_id = 100

    def isConnected(self) -> bool:
        return True

    def openTrades(self) -> list[FakeIBTrade]:
        return list(self.open_trades)

    def cancelOrder(self, order: FakeIBOrder) -> None:
        self.cancelled.append(order.orderId)

    def placeOrder(self, contract, ib_order) -> FakeIBTrade:
        self._next_order_id += 1
        placed = FakeIBOrder(orderId=self._next_order_id, action=ib_order.action)
        self.placed.append((contract, ib_order))
        trade = FakeIBTrade(contract=contract, order=placed)
        self.open_trades.append(trade)
        return trade


@pytest.fixture
def fake_ib(monkeypatch) -> FakeIB:
    instance = FakeIB()
    monkeypatch.setattr(ibkr, "IB", lambda: instance)
    return instance


def execution(exec_id: str, *, order_id: int = 101, shares: float = 5.0, price: float = 100.0,
              side: str = "BOT", commission: float | None = 1.25, at: datetime = T0) -> FakeFill:
    return FakeFill(
        contract=FakeContract("AAPL"),
        execution=FakeExecution(execId=exec_id, orderId=order_id, shares=shares, price=price, side=side),
        commissionReport=FakeCommission(commission),
        time=at,
    )


def deliver(broker: IBKRBroker, fill: FakeFill) -> None:
    broker._on_exec_details(None, fill)


# ----- Executions are counted once ----- #

def test_the_same_execution_reported_twice_is_counted_once(fake_ib):
    broker = IBKRBroker()

    deliver(broker, execution("e1"))
    deliver(broker, execution("e1"))

    fills = broker.get_fills()
    assert len(fills) == 1
    assert fills[0].quantity == 5.0


def test_an_execution_already_handed_over_is_not_handed_over_again(fake_ib):
    broker = IBKRBroker()

    deliver(broker, execution("e1"))
    assert len(broker.get_fills()) == 1

    deliver(broker, execution("e1"))
    assert broker.get_fills() == []


def test_distinct_executions_all_come_through(fake_ib):
    broker = IBKRBroker()

    deliver(broker, execution("e1"))
    deliver(broker, execution("e2", shares=3.0))

    assert [fill.quantity for fill in broker.get_fills()] == [5.0, 3.0]


def test_a_fill_carries_its_commission_and_side(fake_ib):
    broker = IBKRBroker()

    deliver(broker, execution("e1", commission=2.5))
    deliver(broker, execution("e2", side="SLD", commission=None))
    fills = broker.get_fills()

    assert (fills[0].commission, fills[0].side) == (2.5, SignalType.BUY)
    assert (fills[1].commission, fills[1].side) == (None, SignalType.SELL)


# ----- Resting orders are visible to the risk rules ----- #

def test_open_orders_come_from_what_the_venue_is_working(fake_ib):
    fake_ib.open_trades = [
        FakeIBTrade(FakeContract("AAPL"), FakeIBOrder(1, "BUY", 10.0, 99.5), _remaining=4.0),
        FakeIBTrade(FakeContract("MSFT"), FakeIBOrder(2, "SELL", 5.0, 50.0), _remaining=5.0),
        FakeIBTrade(FakeContract("AAPL"), FakeIBOrder(3, "BUY", 8.0, 99.0), _remaining=0.0),
    ]
    broker = IBKRBroker()

    assert isinstance(broker, OpenOrderQueries)
    views = broker.open_order_views()
    # The fully filled one is not resting any more.
    assert {view.broker_order_id for view in views} == {"1", "2"}

    aapl = broker.open_order_views("AAPL")
    assert len(aapl) == 1
    assert aapl[0].remaining_quantity == 4.0
    assert aapl[0].limit_price == 99.5
    assert aapl[0].order.side is SignalType.BUY


def test_an_order_this_process_submitted_keeps_its_own_metadata(fake_ib):
    broker = IBKRBroker()
    submitted = Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0, ref_price=99.5,
        limit_price=99.5, metadata={"order_slot": "ladder-1"},
    )

    broker_order_id = broker.submit(submitted)
    view = broker.open_order_views("AAPL")[0]

    assert view.broker_order_id == broker_order_id
    assert view.order is submitted
    assert view.order.metadata["order_slot"] == "ladder-1"


def test_an_order_placed_before_this_process_started_is_still_reported(fake_ib):
    fake_ib.open_trades = [FakeIBTrade(FakeContract("AAPL"), FakeIBOrder(7, "SELL", 3.0, 101.0), _remaining=3.0)]
    broker = IBKRBroker()

    view = broker.open_order_views("AAPL")[0]

    assert view.order.side is SignalType.SELL
    assert view.order.quantity == 3.0
    assert view.order.limit_price == 101.0


def test_resting_orders_can_be_cancelled_by_symbol_and_side(fake_ib):
    fake_ib.open_trades = [
        FakeIBTrade(FakeContract("AAPL"), FakeIBOrder(1, "BUY", 10.0, 99.5)),
        FakeIBTrade(FakeContract("AAPL"), FakeIBOrder(2, "SELL", 10.0, 101.0)),
        FakeIBTrade(FakeContract("MSFT"), FakeIBOrder(3, "BUY", 10.0, 50.0)),
    ]
    broker = IBKRBroker()

    assert broker.cancel_open_orders("AAPL", side=SignalType.BUY) == 1
    assert fake_ib.cancelled == [1]

    assert broker.cancel_open_orders("AAPL") == 2
    assert fake_ib.cancelled == [1, 1, 2]


def test_submitting_without_a_connection_is_refused(fake_ib, monkeypatch):
    broker = IBKRBroker()
    monkeypatch.setattr(fake_ib, "isConnected", lambda: False)

    with pytest.raises(RuntimeError):
        broker.submit(Order(time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=1.0, ref_price=1.0))
