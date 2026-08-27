import asyncio
from datetime import date, datetime

import pytest

from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core.contracts import Contract, ContractRegistry, Right, Settlement
from ghosalquant.core.types import BookLevel, OrderBook, Signal, SignalType
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk.risk_manager import RiskManager

T0 = datetime(2026, 9, 1, 14, 30)
OCC = "AAPL  260116C00150000"


def option(symbol: str = OCC) -> Contract:
    return Contract(
        symbol=symbol, underlier="AAPL", multiplier=100.0, right=Right.CALL,
        strike=150.0, expiry=date(2026, 1, 16), settlement=Settlement.PHYSICAL,
    )


# ----- A symbol nobody described is a share ----- #

def test_an_unregistered_symbol_is_a_share_of_itself():
    contract = ContractRegistry().get("AAPL")

    assert contract == Contract(symbol="AAPL", underlier="AAPL")
    assert contract.multiplier == 1.0
    assert contract.right is None


def test_a_registry_knows_what_it_was_told_and_nothing_more():
    registry = ContractRegistry([option()])

    assert OCC in registry
    assert "AAPL" not in registry
    assert registry.get(OCC).strike == 150.0
    assert registry.get("AAPL").multiplier == 1.0


def test_a_contract_can_be_registered_later():
    registry = ContractRegistry()
    registry.register(option())

    assert registry.get(OCC).right is Right.CALL
    assert registry.contracts == (option(),)


# ----- A contract has to make sense ----- #

def test_a_contract_that_could_not_trade_is_refused():
    with pytest.raises(ValueError):
        Contract(symbol="X", underlier="X", multiplier=0.0)
    with pytest.raises(ValueError):
        Contract(symbol="X", underlier="X", strike=-1.0)
    # An option without a strike or an expiry is not one.
    with pytest.raises(ValueError):
        Contract(symbol="X", underlier="AAPL", right=Right.CALL, strike=150.0)


# ----- OCC symbols, when asked ----- #

def test_an_occ_symbol_can_be_read_when_asked():
    contract = Contract.from_occ(OCC)

    assert contract.underlier == "AAPL"
    assert contract.right is Right.CALL
    assert contract.strike == 150.0
    assert contract.expiry == date(2026, 1, 16)
    assert contract.multiplier == 100.0


def test_a_put_and_a_fractional_strike_read_correctly():
    contract = Contract.from_occ("SPY   270319P00432500")

    assert (contract.underlier, contract.right) == ("SPY", Right.PUT)
    assert contract.strike == 432.5


def test_a_symbol_is_never_read_as_an_option_on_its_own():
    # The shape suggests an option; the registry was not told, so it is a share.
    assert ContractRegistry().get(OCC).multiplier == 1.0

    with pytest.raises(ValueError):
        Contract.from_occ("AAPL")
    with pytest.raises(ValueError):
        Contract.from_occ("AAPL  260116X00150000")


# ----- It reaches the parts that will need it ----- #

class ContextWatcher(OrderBookStrategy):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list = []

    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_order_book(self, book, context=None):
        self.seen.append(context)
        return Signal(
            time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
            ref_price=book.best_bid.price, confidence=1.0, quantity=1.0,
            limit_price=book.best_bid.price,
        )


def test_a_running_system_carries_the_registry_to_the_strategy_and_the_rules():
    seen_risk: list = []

    class Recorder:
        def apply(self, _draft, context) -> bool:
            seen_risk.append(context)
            return True

    strategy = ContextWatcher()
    book = OrderBook(
        time_stamp=T0, symbol=OCC,
        bids=(BookLevel(5.0, 50.0),), asks=(BookLevel(5.10, 50.0),),
    )
    asyncio.run(ExecutionSystem(
        ListDataSource([book]), strategy,
        portfolio=BasicPortfolio(init_capital=50_000.0),
        risk_manager=RiskManager(rules=[Recorder()]),
        broker=OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=1),
        contracts=ContractRegistry([option()]),
    ).run())

    assert strategy.seen[0].contract.strike == 150.0
    assert strategy.seen[0].contract.underlier == "AAPL"
    assert seen_risk[0].contracts.get(OCC).multiplier == 100.0


def test_a_system_given_no_registry_still_sees_every_symbol_as_a_share():
    strategy = ContextWatcher()
    book = OrderBook(
        time_stamp=T0, symbol="AAPL",
        bids=(BookLevel(100.0, 50.0),), asks=(BookLevel(100.02, 50.0),),
    )
    asyncio.run(ExecutionSystem(
        ListDataSource([book]), strategy,
        portfolio=BasicPortfolio(init_capital=50_000.0),
        risk_manager=RiskManager(),
        broker=OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=1),
    ).run())

    assert strategy.seen[0].contract == Contract(symbol="AAPL", underlier="AAPL")
