import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pytest

from ghosalquant.brokerage import ibkr
from ghosalquant.brokerage.ibkr import IBKRBroker, ib_contract, ib_symbol
from ghosalquant.brokerage.open_orders import OpenOrderQueries
from ib_insync import Stock

from ghosalquant.core import metadata_keys
from ghosalquant.core.contracts import Contract as TradedContract, ContractRegistry, Right
from ghosalquant.core.types import Bar, BookLevel, Order, OrderBook, Signal, SignalType
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk import (
    CashAllocationConfig,
    CashAllocationRule,
    IntegerQuantity,
    MaxSymbolExposure,
    OrderDraft,
    RiskManager,
    SymbolCapitalFractionSizer,
)

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)


# ----- A stand-in for ib_insync, so none of this needs a gateway ----- #

@dataclass
class FakeContract:
    symbol: str
    sec_type: str = "STK"
    local_symbol: str = ""

    # ib_insync spells these in camelCase; the broker reads them by those names.
    @property
    def secType(self) -> str:
        return self.sec_type

    @property
    def localSymbol(self) -> str:
        return self.local_symbol


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



@dataclass
class FakeAccountValue:
    tag: str
    value: str


@dataclass
class FakePosition:
    contract: FakeContract
    position: float
    avgCost: float


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
        self.account_values: list[FakeAccountValue] = []
        self.account_positions: list[FakePosition] = []
        self.connected = True
        self._next_order_id = 100

    def isConnected(self) -> bool:
        return self.connected

    async def connectAsync(self, host=None, port=None, clientId=None) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def accountValues(self) -> list[FakeAccountValue]:
        return list(self.account_values)

    def positions(self) -> list[FakePosition]:
        return list(self.account_positions)

    def openTrades(self) -> list[FakeIBTrade]:
        return list(self.open_trades)

    def cancelOrder(self, order: FakeIBOrder) -> None:
        self.cancelled.append(order.orderId)

    def placeOrder(self, contract, ib_order) -> FakeIBTrade:
        self._next_order_id += 1
        # Carries the real quantity and limit: the broker checks a stored order still describes
        # what the venue reports, so a fake that drops them hides that check.
        placed = FakeIBOrder(
            orderId=self._next_order_id,
            action=ib_order.action,
            totalQuantity=float(ib_order.totalQuantity),
            lmtPrice=float(getattr(ib_order, "lmtPrice", 0.0) or 0.0),
        )
        self.placed.append((contract, ib_order))
        trade = FakeIBTrade(contract=contract, order=placed, _remaining=float(ib_order.totalQuantity))
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


def _book(seconds: int, price: float = 100.0, symbol: str = "AAPL") -> OrderBook:
    return OrderBook(
        time_stamp=T0 + timedelta(seconds=seconds), symbol=symbol,
        bids=(BookLevel(price, 500.0),), asks=(BookLevel(price + 0.02, 500.0),),
    )


class _Quoter(OrderBookStrategy):
    """Bids on every book, tagging the order so the cash buckets can see it."""

    def __init__(self) -> None:
        super().__init__()
        self.accepted: list = []

    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_order_book(self, book, context=None):
        if book.best_bid is None:
            return None
        return Signal(
            time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
            ref_price=book.best_bid.price, confidence=1.0, quantity=1.0,
            limit_price=book.best_bid.price,
            metadata={metadata_keys.ALLOCATION_BUCKET: "ladder"},
        )

    def on_order_accepted(self, order, broker_order_id) -> None:
        self.accepted.append((order, broker_order_id))


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


# ----- Surviving a restart ----- #

def test_a_resting_order_keeps_its_strategy_metadata_across_a_restart(tmp_path, fake_ib):
    state = tmp_path / "ibkr.json"
    broker = IBKRBroker(state_path=state)
    submitted = Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0, ref_price=99.5,
        limit_price=99.5, metadata={"order_slot": "ladder-1", "allocation_bucket": "ladder"},
    )
    broker.submit(submitted)

    # A new process; IB still reports the order as working.
    restarted = IBKRBroker(state_path=state)
    view = restarted.open_order_views("AAPL")[0]

    assert view.order.metadata["order_slot"] == "ladder-1"
    assert view.order.metadata["allocation_bucket"] == "ladder"


def test_without_a_state_path_the_metadata_is_lost_and_the_order_is_rebuilt(tmp_path, fake_ib):
    broker = IBKRBroker()
    broker.submit(Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0, ref_price=99.5,
        limit_price=99.5, metadata={"order_slot": "ladder-1"},
    ))

    restarted = IBKRBroker()
    view = restarted.open_order_views("AAPL")[0]

    # Which is why state_path matters once the cash rules are counting buckets.
    assert view.order.metadata in (None, {})


def test_a_stored_order_is_ignored_when_it_does_not_describe_what_the_venue_reports(tmp_path, fake_ib):
    state = tmp_path / "ibkr.json"
    broker = IBKRBroker(state_path=state)
    broker.submit(Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0, ref_price=99.5,
        limit_price=99.5, metadata={"order_slot": "ladder-1"},
    ))
    reused_id = fake_ib.open_trades[0].order.orderId

    # IB order ids restart with the session: the same id, a different order.
    fake_ib.open_trades = [
        FakeIBTrade(FakeContract("MSFT"), FakeIBOrder(reused_id, "SELL", 4.0, 50.0), _remaining=4.0)
    ]
    restarted = IBKRBroker(state_path=state)
    view = restarted.open_order_views("MSFT")[0]

    assert view.order.symbol == "MSFT"
    assert view.order.side is SignalType.SELL
    assert view.order.metadata in (None, {})


# ----- An order is written down before it is sent ----- #

def test_an_order_lost_to_a_timeout_is_reported_rather_than_forgotten(tmp_path, fake_ib, monkeypatch):
    state = tmp_path / "ibkr.json"
    broker = IBKRBroker(state_path=state)

    def timeout(*_args, **_kwargs):
        raise TimeoutError("no acknowledgement")

    monkeypatch.setattr(fake_ib, "placeOrder", timeout)
    with pytest.raises(TimeoutError):
        broker.submit(Order(
            time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0, ref_price=99.5,
            limit_price=99.5,
        ))

    # It may or may not be live at the venue, so the next start is told about it.
    restarted = IBKRBroker(state_path=state)
    indeterminate = restarted.indeterminate_orders()
    assert len(indeterminate) == 1
    assert indeterminate[0].symbol == "AAPL"
    assert indeterminate[0].quantity == 10.0


def test_an_acknowledged_order_leaves_nothing_indeterminate(tmp_path, fake_ib):
    state = tmp_path / "ibkr.json"
    broker = IBKRBroker(state_path=state)
    broker.submit(Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0, ref_price=99.5,
        limit_price=99.5,
    ))

    assert broker.indeterminate_orders() == ()
    assert IBKRBroker(state_path=state).indeterminate_orders() == ()


def test_a_fill_carries_the_execution_id_behind_it(fake_ib):
    broker = IBKRBroker()

    deliver(broker, execution("e1"))

    assert broker.get_fills()[0].execution_id == "e1"


# ----- What the account actually holds ----- #

def test_the_account_snapshot_is_shaped_for_the_portfolio(fake_ib):
    fake_ib.account_values = [FakeAccountValue("TotalCashValue", "25000.50")]
    fake_ib.account_positions = [
        FakePosition(FakeContract("AAPL"), 10.0, 99.0),
        FakePosition(FakeContract("MSFT"), 0.0, 50.0),
    ]
    broker = IBKRBroker()

    cash, positions = broker.account_snapshot()

    assert cash == 25000.50
    assert positions == {"AAPL": (10.0, 99.0)}


# ----- The whole live path, wired together ----- #

def test_a_live_session_adopts_the_account_and_sizes_against_it(tmp_path, fake_ib):
    """
    The chain the capital-aware rules depend on: broker snapshot -> portfolio -> RiskContext.

    Each link is unit tested above; this is the one that catches them not being connected.
    """
    fake_ib.account_values = [FakeAccountValue("TotalCashValue", "50000")]
    fake_ib.account_positions = [FakePosition(FakeContract("AAPL"), 20.0, 95.0)]
    broker = IBKRBroker(state_path=tmp_path / "ibkr.json")

    cash, positions = broker.account_snapshot()
    portfolio = BasicPortfolio(init_capital=1.0)
    portfolio.adopt(cash, positions, time_stamp=T0)
    assert portfolio.current_equity == pytest.approx(50_000.0 + 20 * 95.0)

    strategy = _Quoter()
    system = ExecutionSystem(
        ListDataSource([_book(second) for second in range(3)]), strategy,
        portfolio=portfolio,
        risk_manager=RiskManager(rules=[
            SymbolCapitalFractionSizer(capital=51_900.0, default_max_notional_fraction=0.10),
            MaxSymbolExposure(capital=51_900.0, max_notional_fraction=0.10),
            CashAllocationRule(
                CashAllocationConfig(total_fraction=0.5, max_symbol_fraction=0.2),
                capital=51_900.0,
            ),
            IntegerQuantity(),
        ]),
        broker=broker,
    )
    asyncio.run(system.run())

    # The inherited position is now marked at the book mid rather than its entry cost.
    assert portfolio.current_equity == pytest.approx(50_000.0 + 20 * 100.01)
    assert strategy.accepted, "the strategy should have had orders accepted"

    context = system._build_risk_context()
    assert context.current_equity is not None
    assert len(context.open_orders) == len(broker.open_order_views("AAPL"))
    assert all(
        (view.order.metadata or {}).get(metadata_keys.ALLOCATION_BUCKET) == "ladder"
        for view in context.open_orders
    )

    # The exposure rule counts the resting bids on top of the inherited position.
    draft = OrderDraft(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=100.0, price=100.0
    )
    MaxSymbolExposure(capital=51_900.0, max_notional_fraction=0.10).apply(draft, context)
    assert draft.metadata["symbol_exposure_committed_notional"] > 20 * 95.0


def test_an_execution_moves_the_position_once_through_the_whole_chain(tmp_path, fake_ib):
    portfolio = BasicPortfolio(init_capital=10_000.0)
    broker = IBKRBroker()
    broker.submit(Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=5.0, ref_price=100.0,
        limit_price=100.0,
    ))
    reported = execution("X1", shares=2.0)

    deliver(broker, reported)
    deliver(broker, reported)
    for fill in broker.get_fills():
        asyncio.run(portfolio.process_fill(fill))
    once = portfolio.open_qty_by_symbol.get("AAPL", 0.0)

    # And again with a broker that has forgotten: the portfolio still refuses it.
    broker._seen_exec_ids.clear()
    deliver(broker, reported)
    for fill in broker.get_fills():
        asyncio.run(portfolio.process_fill(fill))

    assert once == 2.0
    assert portfolio.open_qty_by_symbol.get("AAPL", 0.0) == 2.0


# ----- Options reach the venue as options ----- #

OCC = "AAPL  260116C00150000"


def _call() -> TradedContract:
    return TradedContract(
        symbol=OCC, underlier="AAPL", multiplier=100.0, right=Right.CALL,
        strike=150.0, expiry=date(2026, 1, 16),
    )


def _option_order(symbol: str = OCC) -> Order:
    return Order(
        time_stamp=T0, symbol=symbol, side=SignalType.BUY, quantity=1.0,
        ref_price=5.0, limit_price=5.0,
    )


def test_a_registered_option_is_submitted_as_an_option():
    contract = ib_contract(_call())

    assert contract.secType == "OPT"
    assert contract.symbol == "AAPL"
    assert contract.lastTradeDateOrContractMonth == "20260116"
    assert (contract.strike, contract.right, contract.multiplier) == (150.0, "C", "100")


def test_anything_not_registered_is_still_submitted_as_a_share(fake_ib):
    broker = IBKRBroker()
    broker.submit(Order(
        time_stamp=T0, symbol="AAPL", side=SignalType.BUY, quantity=1.0, ref_price=100.0,
    ))

    assert fake_ib.placed[0][0].secType == "STK"


def test_the_broker_builds_the_option_from_its_registry(fake_ib):
    broker = IBKRBroker(contracts=ContractRegistry([_call()]))
    broker.submit(_option_order())

    assert fake_ib.placed[0][0].secType == "OPT"
    assert fake_ib.placed[0][0].strike == 150.0


def test_an_explicit_contract_override_still_wins(fake_ib):
    override = Stock("AAPL", "SMART", "USD")
    override.symbol = OCC
    broker = IBKRBroker(contract_overrides=[override], contracts=ContractRegistry([_call()]))
    broker.submit(_option_order())

    assert fake_ib.placed[0][0].secType == "STK"


# ----- And come back under the symbol we know them by ----- #

def test_an_option_is_named_by_its_local_symbol_not_its_underlier():
    option_contract = FakeContract("AAPL", sec_type="OPT", local_symbol=OCC)

    assert ib_symbol(option_contract) == OCC
    assert ib_symbol(FakeContract("AAPL")) == "AAPL"


def test_an_option_execution_is_reported_against_the_option(fake_ib):
    broker = IBKRBroker(contracts=ContractRegistry([_call()]))
    deliver(broker, FakeFill(
        contract=FakeContract("AAPL", sec_type="OPT", local_symbol=OCC),
        execution=FakeExecution(execId="x1", orderId=1, shares=1.0, price=5.0),
        commissionReport=FakeCommission(0.65), time=T0,
    ))

    # Reporting it as "AAPL" would credit the fill to the stock position.
    assert broker.get_fills()[0].symbol == OCC


def test_option_and_stock_orders_are_told_apart(fake_ib):
    fake_ib.open_trades = [
        FakeIBTrade(FakeContract("AAPL", sec_type="OPT", local_symbol=OCC),
                    FakeIBOrder(1, "BUY", 1.0, 5.0), _remaining=1.0),
        FakeIBTrade(FakeContract("AAPL"), FakeIBOrder(2, "BUY", 100.0, 150.0), _remaining=100.0),
    ]
    broker = IBKRBroker(contracts=ContractRegistry([_call()]))

    assert [view.broker_order_id for view in broker.open_order_views(OCC)] == ["1"]
    assert [view.broker_order_id for view in broker.open_order_views("AAPL")] == ["2"]

    assert broker.cancel_open_orders(OCC) == 1
    assert fake_ib.cancelled == [1]


def test_an_option_goes_out_as_an_option_and_comes_back_as_one(fake_ib):
    """
    The live path end to end: registry -> IBKR contract -> execution -> position.

    Everything between the strategy and the venue treats the symbol as opaque, so this is the only
    place the OCC symbol has to survive a round trip through IBKR's own naming.
    """
    registry = ContractRegistry([TradedContract.from_occ(OCC)])
    portfolio = BasicPortfolio(init_capital=100_000.0, contracts=registry)
    broker = IBKRBroker(contracts=registry)
    system = ExecutionSystem(
        ListDataSource([_book(0, price=5.0, symbol=OCC)]), _Quoter(),
        portfolio=portfolio, broker=broker, contracts=registry,
        risk_manager=RiskManager(rules=[IntegerQuantity()]),
    )
    asyncio.run(system.run())

    sent, _ = fake_ib.placed[0]
    assert (sent.secType, sent.symbol, sent.strike) == ("OPT", "AAPL", 150.0)

    deliver(broker, FakeFill(
        contract=FakeContract("AAPL", sec_type="OPT", local_symbol=OCC),
        execution=FakeExecution(execId="e1", orderId=1, shares=1.0, price=5.0),
        commissionReport=FakeCommission(0.65), time=T0,
    ))
    for fill in broker.get_fills():
        asyncio.run(portfolio.process_fill(fill))

    assert portfolio.open_qty_by_symbol == {OCC: 1.0}
    # One contract of premium is 100 shares' worth, not one.
    assert portfolio.cash_balance == pytest.approx(100_000.0 - 500.0 - 0.65)


PUT = "AAPL  260116P00150000"


def test_the_account_is_read_with_options_named_priced_and_signed_as_they_are_held(fake_ib):
    """
    IB describes an option by its underlier, prices it per contract, and signs a short negative.

    Taken literally that is a long AAPL stock position at 100x the premium; every part of this has
    to be undone before the numbers mean anything.
    """
    fake_ib.account_values = [FakeAccountValue("TotalCashValue", "100000")]
    fake_ib.account_positions = [
        FakePosition(FakeContract("AAPL", sec_type="OPT", local_symbol=PUT), -2.0, 300.0),
        FakePosition(FakeContract("AAPL"), 100.0, 148.0),
        FakePosition(FakeContract("MSFT"), 0.0, 50.0),
    ]
    broker = IBKRBroker(contracts=ContractRegistry([TradedContract.from_occ(PUT)]))

    cash, positions = broker.account_snapshot()

    assert cash == 100_000.0
    assert positions == {PUT: (-2.0, 3.0), "AAPL": (100.0, 148.0)}


def test_a_sold_put_is_carried_from_the_account_through_to_assignment(fake_ib):
    """
    The whole options path in one run: IBKR account -> portfolio -> expiry -> stock.

    Two puts sold for 3.00 against a 150 strike, expiring with AAPL at 145. The 1,000 lost on
    assignment is offset by the 600 already collected, so the account ends 400 down.
    """
    registry = ContractRegistry([TradedContract.from_occ(PUT)])
    fake_ib.account_values = [FakeAccountValue("TotalCashValue", "100000")]
    fake_ib.account_positions = [
        FakePosition(FakeContract("AAPL", sec_type="OPT", local_symbol=PUT), -2.0, 300.0)
    ]
    broker = IBKRBroker(contracts=registry)
    portfolio = BasicPortfolio(init_capital=1.0, contracts=registry)

    cash, positions = broker.account_snapshot()
    portfolio.adopt(cash, positions, time_stamp=datetime(2026, 1, 15, tzinfo=timezone.utc))
    assert portfolio.open_qty_by_symbol == {PUT: -2.0}
    assert portfolio.current_equity == pytest.approx(99_400.0)

    system = ExecutionSystem(
        ListDataSource([_underlier(15, 152.0), _underlier(16, 145.0), _underlier(20, 145.0)]),
        _Idle(), portfolio=portfolio, broker=broker, contracts=registry,
        risk_manager=RiskManager(rules=[]),
    )
    asyncio.run(system.run())

    assert portfolio.open_qty_by_symbol == {"AAPL": 200.0}
    assert portfolio.cash_balance == pytest.approx(100_000.0 - 200 * 150.0)
    assert portfolio.realised_pnl == pytest.approx(600.0)
    assert portfolio.current_equity == pytest.approx(99_000.0)


class _Idle(OrderBookStrategy):
    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_market_data(self, event, context=None): return None

    def on_order_book(self, book, context=None): return None


def _underlier(day: int, price: float) -> Bar:
    at = datetime(2026, 1, day, 21, 0, tzinfo=timezone.utc)
    return Bar(time_stamp=at, symbol="AAPL", open=price, high=price, low=price,
               close=price, volume=1_000.0)
