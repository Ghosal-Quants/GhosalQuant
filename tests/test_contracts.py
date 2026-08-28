import asyncio
from datetime import date, datetime, timedelta

import pytest

from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core.contracts import Contract, ContractRegistry, Right, Settlement
from ghosalquant.core.types import BookLevel, Fill, OpenOrderView, Order, OrderBook, Signal, SignalType
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk import (
    CashAllocationConfig,
    CashAllocationRule,
    MaxSymbolExposure,
    OrderDraft,
    RiskContext,
    RiskManager,
    SymbolCapitalFractionSizer,
    resting_buy_notional,
)

T0 = datetime(2026, 9, 1, 14, 30)
OCC = "AAPL  260116C00150000"


def draft(*, symbol: str, quantity: float | None = 10.0, price: float = 100.0) -> OrderDraft:
    return OrderDraft(
        time_stamp=T0, symbol=symbol, side=SignalType.BUY, quantity=quantity, price=price,
    )


def resting(symbol: str, quantity: float, limit_price: float) -> OpenOrderView:
    order = Order(
        time_stamp=T0, symbol=symbol, side=SignalType.BUY, quantity=quantity,
        ref_price=limit_price, limit_price=limit_price,
    )
    return OpenOrderView(
        broker_order_id="id", order=order, remaining_quantity=quantity,
        submitted_at=T0, limit_price=limit_price,
    )


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


# ----- One contract is not one share ----- #

_REGISTRY = ContractRegistry([option()])


def _fill(side: SignalType, quantity: float, price: float, symbol: str = OCC) -> Fill:
    return Fill(time_stamp=T0, symbol=symbol, side=side, quantity=quantity, price=price)


def test_buying_a_contract_costs_the_multiplier_times_the_premium():
    portfolio = BasicPortfolio(init_capital=10_000.0, contracts=_REGISTRY)

    asyncio.run(portfolio.process_fill(_fill(SignalType.BUY, 2.0, 5.00)))

    # Two contracts at $5.00 is $1,000, not $10.
    assert portfolio.cash_balance == pytest.approx(9_000.0)
    assert portfolio.open_qty_by_symbol[OCC] == 2.0
    assert portfolio.open_cost_by_symbol[OCC] == pytest.approx(1_000.0)


def test_the_average_entry_price_stays_a_premium_not_a_notional():
    portfolio = BasicPortfolio(init_capital=10_000.0, contracts=_REGISTRY)
    asyncio.run(portfolio.process_fill(_fill(SignalType.BUY, 2.0, 5.00)))

    assert portfolio.average_entry_price(OCC) == pytest.approx(5.00)


def test_a_contract_is_marked_and_realised_at_the_multiplier():
    portfolio = BasicPortfolio(init_capital=10_000.0, contracts=_REGISTRY)
    asyncio.run(portfolio.process_fill(_fill(SignalType.BUY, 1.0, 5.00)))

    portfolio.mark(OCC, 6.00, T0)
    portfolio.snapshot_equity(time_stamp=T0, prices={OCC: 6.00})
    assert portfolio.unrealised_pnl == pytest.approx(100.0)

    asyncio.run(portfolio.process_fill(_fill(SignalType.SELL, 1.0, 6.00)))
    assert portfolio.realised_pnl == pytest.approx(100.0)
    assert portfolio.cash_balance == pytest.approx(10_100.0)


def test_a_share_is_unchanged_by_any_of_this():
    portfolio = BasicPortfolio(init_capital=10_000.0, contracts=_REGISTRY)

    asyncio.run(portfolio.process_fill(_fill(SignalType.BUY, 2.0, 5.00, symbol="AAPL")))

    assert portfolio.cash_balance == pytest.approx(9_990.0)
    assert portfolio.average_entry_price("AAPL") == pytest.approx(5.00)


# ----- The rules price a contract the same way ----- #

def test_a_notional_sizer_buys_contracts_not_shares():
    rule = SymbolCapitalFractionSizer(capital=100_000.0, default_max_notional_fraction=0.01)
    order = draft(symbol=OCC, price=5.00)

    rule.apply(order, RiskContext(contracts=_REGISTRY))

    # 1% of 100,000 is 1,000, which buys two contracts at $500 each, not 200.
    assert order.quantity == pytest.approx(2.0)


def test_exposure_counts_a_contract_at_its_full_notional():
    rule = MaxSymbolExposure(capital=100_000.0, max_notional_fraction=0.01)
    order = draft(symbol=OCC, quantity=100.0, price=5.00)

    assert rule.apply(order, RiskContext(contracts=_REGISTRY)) is True
    assert order.quantity == pytest.approx(2.0)


def test_a_resting_contract_order_reserves_its_full_notional():
    resting_option = resting(OCC, 1.0, 5.00)

    assert resting_buy_notional([resting_option]) == pytest.approx(5.0)
    assert resting_buy_notional([resting_option], contracts=_REGISTRY) == pytest.approx(500.0)


def test_cash_allocation_measures_resting_contracts_at_their_real_reservation():
    rule = CashAllocationRule(
        CashAllocationConfig(total_fraction=1.0, max_symbol_fraction=0.02), capital=100_000.0
    )
    context = RiskContext(open_orders=(resting(OCC, 2.0, 5.00),), contracts=_REGISTRY)
    order = draft(symbol=OCC, quantity=100.0, price=5.00)

    assert rule.apply(order, context) is True
    # 2% of 100,000 is 2,000, of which one resting contract pair already holds 1,000.
    assert order.quantity == pytest.approx(2.0)


# ----- Selling what you do not hold ----- #

_SHORT_T0 = datetime(2026, 10, 1, 14, 30)


def _short_fill(side: SignalType, quantity: float, price: float, symbol: str = "AAPL") -> Fill:
    return Fill(
        time_stamp=_SHORT_T0, symbol=symbol, side=side, quantity=quantity, price=price
    )


def test_selling_with_nothing_held_opens_a_short_and_receives_the_premium():
    portfolio = BasicPortfolio(init_capital=10_000.0)

    asyncio.run(portfolio.process_fill(_short_fill(SignalType.SELL, 10.0, 50.0)))

    assert portfolio.cash_balance == pytest.approx(10_500.0)
    assert portfolio.open_qty_by_symbol["AAPL"] == pytest.approx(-10.0)
    assert portfolio.average_entry_price("AAPL") == pytest.approx(50.0)


def test_a_short_gains_when_the_price_falls_and_loses_when_it_rises():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.SELL, 10.0, 50.0)))

    portfolio.snapshot_equity(time_stamp=_SHORT_T0, prices={"AAPL": 60.0})
    assert portfolio.unrealised_pnl == pytest.approx(-100.0)
    assert portfolio.current_equity == pytest.approx(9_900.0)

    portfolio.snapshot_equity(time_stamp=_SHORT_T0 + timedelta(seconds=1), prices={"AAPL": 45.0})
    assert portfolio.unrealised_pnl == pytest.approx(50.0)


def test_buying_a_short_back_realises_the_difference():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.SELL, 10.0, 50.0)))
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.BUY, 10.0, 45.0)))

    assert portfolio.realised_pnl == pytest.approx(50.0)
    assert portfolio.cash_balance == pytest.approx(10_050.0)
    assert portfolio.open_qty_by_symbol.get("AAPL", 0.0) == 0.0


def test_a_fill_larger_than_the_position_reverses_it_through_flat():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.BUY, 2.0, 100.0)))
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.SELL, 3.0, 110.0)))

    # Two closed at a profit, and the third opens a short rather than being dropped.
    assert portfolio.realised_pnl == pytest.approx(20.0)
    assert portfolio.open_qty_by_symbol["AAPL"] == pytest.approx(-1.0)
    assert portfolio.average_entry_price("AAPL") == pytest.approx(110.0)


def test_a_short_option_is_worth_its_multiplier():
    portfolio = BasicPortfolio(init_capital=10_000.0, contracts=_REGISTRY)

    asyncio.run(portfolio.process_fill(_short_fill(SignalType.SELL, 1.0, 5.00, symbol=OCC)))
    assert portfolio.cash_balance == pytest.approx(10_500.0)

    portfolio.snapshot_equity(time_stamp=_SHORT_T0, prices={OCC: 6.00})
    assert portfolio.unrealised_pnl == pytest.approx(-100.0)

    asyncio.run(portfolio.process_fill(_short_fill(SignalType.BUY, 1.0, 6.00, symbol=OCC)))
    assert portfolio.realised_pnl == pytest.approx(-100.0)
    assert portfolio.cash_balance == pytest.approx(9_900.0)


def test_commission_is_charged_on_both_legs_of_a_short():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    open_fill = Fill(
        time_stamp=_SHORT_T0, symbol="AAPL", side=SignalType.SELL, quantity=10.0,
        price=50.0, commission=1.0,
    )
    close_fill = Fill(
        time_stamp=_SHORT_T0, symbol="AAPL", side=SignalType.BUY, quantity=10.0,
        price=50.0, commission=1.0,
    )

    asyncio.run(portfolio.process_fill(open_fill))
    asyncio.run(portfolio.process_fill(close_fill))

    assert portfolio.fees_paid == pytest.approx(2.0)
    assert portfolio.realised_pnl == pytest.approx(-2.0)
    assert portfolio.cash_balance == pytest.approx(9_998.0)


def test_a_long_still_behaves_exactly_as_before():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.BUY, 10.0, 50.0)))

    assert portfolio.cash_balance == pytest.approx(9_500.0)
    assert portfolio.open_qty_by_symbol["AAPL"] == pytest.approx(10.0)

    asyncio.run(portfolio.process_fill(_short_fill(SignalType.SELL, 10.0, 55.0)))
    assert portfolio.realised_pnl == pytest.approx(50.0)
    assert portfolio.cash_balance == pytest.approx(10_050.0)


def test_a_fill_for_nothing_does_nothing():
    """Netting divides the commission across the quantity, so an empty fill has to stop first."""
    portfolio = BasicPortfolio(init_capital=10_000.0)
    asyncio.run(portfolio.process_fill(_short_fill(SignalType.BUY, 10.0, 100.0)))

    empty = _short_fill(SignalType.SELL, 0.0, 100.0)
    empty.commission = 1.0
    asyncio.run(portfolio.process_fill(empty))

    assert portfolio.open_qty_by_symbol == {"AAPL": 10.0}
    assert portfolio.cash_balance == pytest.approx(9_000.0)
