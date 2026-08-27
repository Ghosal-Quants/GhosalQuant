import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from ghosalquant.brokerage.simulated import SimBroker
from ghosalquant.core.types import (
    Bar,
    BookLevel,
    Fill,
    OpenOrderView,
    Order,
    OrderBook,
    Signal,
    SignalType,
    Trade,
)
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import Strategy
from ghosalquant.portfolio import (
    BasicPortfolio,
    LotLadder,
    PortfolioValuationEngine,
    ValuationBasis,
    liquidate_into_bids,
    lot_ladders,
    profitable_bid_liquidation,
)
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.util.backtester_utils import calculate_kpms

T0 = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _trade(id_: int, symbol: str, price: float, size: float, seconds: float = 0.0) -> Trade:
    return Trade(id=id_, symbol=symbol, signal_type=SignalType.BUY, entry_price=price,
                 size=size, open_time=_at(seconds))


def _book(symbol: str, bids, asks=((101.0, 5.0),), seconds: float = 0.0) -> OrderBook:
    return OrderBook(
        time_stamp=_at(seconds),
        symbol=symbol,
        bids=tuple(BookLevel(p, q) for p, q in bids),
        asks=tuple(BookLevel(p, q) for p, q in asks),
    )


def _portfolio(fills, capital: float = 1_000.0) -> BasicPortfolio:
    portfolio = BasicPortfolio(init_capital=capital)
    for fill in fills:
        asyncio.run(portfolio.process_fill(fill))
    return portfolio


def _buy(symbol: str, quantity: float, price: float, seconds: float = 0.0) -> Fill:
    return Fill(time_stamp=_at(seconds), symbol=symbol, side=SignalType.BUY, quantity=quantity, price=price)


# ----- LotLadder ----- #

def test_lot_ladder_is_lifo_and_prices_partial_exits():
    ladder = LotLadder.from_trades([
        _trade(0, "A", 10.0, 5.0, seconds=0),
        _trade(1, "A", 20.0, 5.0, seconds=1),
    ])
    assert ladder.quantity == 10.0
    assert ladder.cost == 150.0
    assert ladder.cost_of(0.0) == 0.0
    assert ladder.cost_of(5.0) == 100.0        # newest lot first
    assert ladder.cost_of(7.5) == 125.0        # 5 @ 20 + 2.5 @ 10
    assert ladder.cost_of(10.0) == 150.0
    assert ladder.cost_of(99.0) == 150.0       # capped at the position
    assert ladder.cost_of(-1.0) == 0.0


def test_empty_lot_ladder():
    ladder = LotLadder.from_trades([])
    assert ladder.quantity == 0.0 and ladder.cost == 0.0 and ladder.cost_of(5.0) == 0.0


def test_lot_ladders_group_by_symbol():
    ladders = lot_ladders([_trade(0, "A", 10.0, 1.0), _trade(1, "B", 5.0, 2.0)])
    assert set(ladders) == {"A", "B"}
    assert ladders["B"].cost == 10.0


# ----- Liquidation helpers ----- #

def test_liquidate_into_bids_walks_levels_once():
    bids = (BookLevel(10.0, 3.0), BookLevel(9.5, 4.0), BookLevel(9.0, 100.0))
    result = liquidate_into_bids(5.0, bids, taker_fee_bps=0.0)
    assert result.sellable_quantity == 5.0
    assert result.gross_proceeds == pytest.approx(3 * 10.0 + 2 * 9.5)
    assert result.levels_used == 2
    assert result.worst_price == 9.5
    assert result.vwap == pytest.approx(49.0 / 5.0)


def test_liquidate_into_bids_thin_book_and_fees():
    thin = liquidate_into_bids(10.0, (BookLevel(10.0, 4.0),), taker_fee_bps=100.0)
    assert thin.sellable_quantity == 4.0
    assert thin.gross_proceeds == 40.0
    assert thin.exit_fee == pytest.approx(0.4)
    assert thin.net_proceeds == pytest.approx(39.6)

    empty = liquidate_into_bids(10.0, ())
    assert empty.sellable_quantity == 0.0 and empty.vwap is None and empty.worst_price is None
    assert liquidate_into_bids(0.0, (BookLevel(10.0, 5.0),)).sellable_quantity == 0.0


def test_profitable_bid_liquidation_stops_at_the_floor():
    bids = (BookLevel(10.0, 2.0), BookLevel(9.0, 2.0), BookLevel(8.0, 100.0))
    result = profitable_bid_liquidation(10.0, bids, minimum_net_unit_proceeds=9.0, taker_fee_bps=0.0)
    assert result.sellable_quantity == 4.0
    assert result.worst_price == 9.0

    none_qualify = profitable_bid_liquidation(10.0, bids, minimum_net_unit_proceeds=11.0)
    assert none_qualify.sellable_quantity == 0.0


# ----- A book is never required ----- #

def test_no_book_falls_back_to_the_mark_and_is_never_zero():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine(taker_fee_bps=10.0)
    snapshot = engine.snapshot(_at(1), portfolio=portfolio)
    valuation = snapshot.symbols[0]

    assert valuation.basis is ValuationBasis.MARKED
    assert not valuation.has_book
    assert valuation.mark_value == 100.0
    assert valuation.risk_inventory_value == 100.0
    assert snapshot.risk_equity == 1_000.0
    assert snapshot.marked_nav == 1_000.0
    assert snapshot.executable_liquidation_equity == 1_000.0
    assert not snapshot.fully_executable
    assert snapshot.marked_symbols == ("A",)


def test_stale_invalidated_and_untradable_books_fall_back_to_the_mark():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])

    stale_engine = PortfolioValuationEngine(stale_after_seconds=30.0)
    stale_engine.observe_book(_book("A", [(9.0, 100.0)], seconds=0))
    fresh = stale_engine.snapshot(_at(10), portfolio=portfolio)
    assert fresh.symbols[0].basis is ValuationBasis.EXECUTABLE
    stale = stale_engine.snapshot(_at(100), portfolio=portfolio)
    assert stale.symbols[0].basis is ValuationBasis.MARKED
    assert stale.symbols[0].book_stale
    assert stale.risk_equity == 1_000.0

    invalidated = PortfolioValuationEngine()
    invalidated.observe_book(_book("A", [(9.0, 100.0)]))
    invalidated.invalidate_book("A")
    assert invalidated.snapshot(_at(1), portfolio=portfolio).symbols[0].basis is ValuationBasis.MARKED
    invalidated.observe_book(_book("A", [(9.0, 100.0)], seconds=2))
    assert invalidated.snapshot(_at(3), portfolio=portfolio).symbols[0].basis is ValuationBasis.EXECUTABLE

    halted = PortfolioValuationEngine()
    halted.observe_book(_book("A", [(9.0, 100.0)]))
    halted.set_tradable("A", False)
    marked = halted.snapshot(_at(1), portfolio=portfolio).symbols[0]
    assert marked.basis is ValuationBasis.MARKED
    assert not marked.tradable


def test_require_book_makes_a_missing_book_an_error():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine(require_book=True)
    with pytest.raises(ValueError, match="no usable order book"):
        engine.snapshot(_at(1), portfolio=portfolio)

    engine.observe_book(_book("A", [(9.0, 100.0)]))
    assert engine.snapshot(_at(1), portfolio=portfolio).fully_executable


def test_bar_only_portfolio_is_valued_normally():
    portfolio = _portfolio([_buy("A", 10.0, 10.0), _buy("B", 5.0, 20.0)])
    engine = PortfolioValuationEngine()
    snapshot = engine.snapshot(_at(1), portfolio=portfolio)

    assert snapshot.basis_counts[ValuationBasis.MARKED] == 2
    assert snapshot.marked_nav == 1_000.0
    assert snapshot.risk_equity == 1_000.0
    assert snapshot.risk_equity == snapshot.marked_nav


# ----- Executable valuation ----- #

def test_executable_valuation_walks_the_book_and_charges_the_exit_fee():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine(taker_fee_bps=100.0)
    engine.observe_book(_book("A", [(9.5, 4.0), (9.0, 6.0)]))
    valuation = engine.snapshot(_at(1), portfolio=portfolio).symbols[0]

    assert valuation.basis is ValuationBasis.EXECUTABLE
    assert valuation.executable_liquidation.sellable_quantity == 10.0
    assert valuation.executable_liquidation.gross_proceeds == pytest.approx(92.0)
    assert valuation.executable_liquidation.exit_fee == pytest.approx(0.92)
    assert valuation.unsold_quantity == 0.0
    assert valuation.executable_pnl_after_exit_fee == pytest.approx(91.08 - 100.0)


def test_risk_equity_caps_a_position_at_what_it_cost():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine()
    engine.observe_book(_book("A", [(50.0, 100.0)]))
    snapshot = engine.snapshot(_at(1), portfolio=portfolio)

    assert snapshot.executable_liquidation_equity == pytest.approx(900.0 + 500.0)
    assert snapshot.risk_equity == pytest.approx(1_000.0)


def test_risk_equity_reflects_a_loss_in_full():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine()
    engine.observe_book(_book("A", [(5.0, 100.0)]))
    snapshot = engine.snapshot(_at(1), portfolio=portfolio)

    assert snapshot.risk_equity == pytest.approx(950.0)
    assert snapshot.risk_equity < snapshot.marked_nav


def test_inventory_with_no_bid_to_sell_into_carries_no_risk_value():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine()
    engine.observe_book(_book("A", [(9.0, 3.0)]))
    valuation = engine.snapshot(_at(1), portfolio=portfolio).symbols[0]

    assert valuation.executable_liquidation.sellable_quantity == 3.0
    assert valuation.unsold_quantity == 7.0
    assert valuation.risk_inventory_value == pytest.approx(27.0)
    assert valuation.sellable_cost == pytest.approx(30.0)


def test_own_resting_bids_are_removed_from_executable_depth():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    order = Order(time_stamp=_at(0), symbol="A", side=SignalType.BUY, quantity=6.0, ref_price=9.0, limit_price=9.0)
    view = OpenOrderView(broker_order_id="1", order=order, remaining_quantity=6.0,
                         submitted_at=_at(0), limit_price=9.0)

    engine = PortfolioValuationEngine(books_include_open_orders=True)
    engine.observe_book(_book("A", [(9.0, 10.0), (8.0, 10.0)]))

    without = engine.snapshot(_at(1), portfolio=portfolio, open_orders=[view]).symbols[0]
    assert without.own_bid_quantity_removed == 6.0
    assert without.external_bids[0] == BookLevel(9.0, 4.0)
    assert without.executable_liquidation.gross_proceeds == pytest.approx(4 * 9.0 + 6 * 8.0)
    assert without.indicative_liquidation.gross_proceeds == pytest.approx(10 * 9.0)
    assert without.basis is ValuationBasis.EXECUTABLE


def test_basis_is_indicative_when_own_orders_are_unknown():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine(books_include_open_orders=True)
    engine.observe_book(_book("A", [(9.0, 100.0)]))
    valuation = engine.snapshot(_at(1), portfolio=portfolio).symbols[0]

    assert valuation.basis is ValuationBasis.INDICATIVE
    assert valuation.has_book
    assert valuation.own_bid_quantity_removed == 0.0


def test_mixed_bases_are_reported():
    portfolio = _portfolio([_buy("A", 10.0, 10.0), _buy("B", 5.0, 20.0)])
    engine = PortfolioValuationEngine()
    engine.observe_book(_book("A", [(9.0, 100.0)]))
    snapshot = engine.snapshot(_at(1), portfolio=portfolio)

    assert snapshot.basis_counts == {ValuationBasis.EXECUTABLE: 1, ValuationBasis.INDICATIVE: 0, ValuationBasis.MARKED: 1}
    assert not snapshot.fully_executable
    assert snapshot.marked_symbols == ("B",)


def test_peak_risk_equity_tracks_the_high_water_mark():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine(init_capital=1_000.0)
    assert engine.peak_risk_equity == 1_000.0

    engine.observe_book(_book("A", [(5.0, 100.0)]))
    engine.snapshot(_at(1), portfolio=portfolio)
    assert engine.peak_risk_equity == 1_000.0

    engine.observe_book(_book("A", [(20.0, 100.0)], seconds=2))
    engine.snapshot(_at(2), portfolio=portfolio)
    assert engine.peak_risk_equity == pytest.approx(1_000.0)
    assert engine.last_snapshot.risk_equity == pytest.approx(1_000.0)


def test_risk_equity_helper_matches_the_snapshot():
    portfolio = _portfolio([_buy("A", 10.0, 10.0)])
    engine = PortfolioValuationEngine()
    engine.observe_book(_book("A", [(9.0, 100.0)]))
    assert engine.risk_equity(_at(1), portfolio=portfolio) == engine.snapshot(_at(1), portfolio=portfolio).risk_equity


def test_engine_settings_are_validated():
    with pytest.raises(ValueError):
        PortfolioValuationEngine(taker_fee_bps=-1.0)
    with pytest.raises(ValueError):
        PortfolioValuationEngine(stale_after_seconds=0.0)
    with pytest.raises(ValueError):
        PortfolioValuationEngine(currency_decimals=-1)


def test_flat_portfolio_values_to_cash():
    engine = PortfolioValuationEngine()
    snapshot = engine.snapshot(_at(1), portfolio=BasicPortfolio(init_capital=500.0))
    assert snapshot.symbols == ()
    assert snapshot.risk_equity == snapshot.marked_nav == 500.0
    assert snapshot.fully_executable


# ----- Attaching the engine changes nothing ----- #

class BuyEveryOtherBar(Strategy):
    def on_start(self) -> None: ...
    def on_end(self) -> None: ...

    def on_market_data(self, bar: Bar):
        if bar.time_stamp.second % 2 == 0:
            return Signal(bar.time_stamp, bar.symbol, SignalType.BUY, bar.close, quantity=2.0)
        return None


def _bar_run(engine: PortfolioValuationEngine | None):
    bars = [
        Bar(time_stamp=_at(second), symbol="A", open=10.0 + second, high=11.0 + second,
            low=9.0 + second, close=10.0 + second, volume=1_000.0)
        for second in range(8)
    ]
    portfolio = BasicPortfolio(init_capital=10_000.0)
    execution = ExecutionSystem(
        data_src=ListDataSource(bars),
        strategy=BuyEveryOtherBar(),
        risk_manager=RiskManager(rules=[]),
        portfolio=portfolio,
        broker=SimBroker(spread_bps=0.0, slippage_std_bps=0.0, impact_bps_at_full_participation=0.0, seed=1),
    )
    asyncio.run(execution.run())
    if engine is not None:
        engine.snapshot(_at(8), portfolio=portfolio)
    return portfolio


def test_attaching_the_engine_to_a_bar_run_changes_nothing():
    without = _bar_run(None)
    engine = PortfolioValuationEngine()
    with_engine = _bar_run(engine)

    assert calculate_kpms(without).as_dict() == calculate_kpms(with_engine).as_dict()
    assert [p.equity for p in without.equity_curve] == [p.equity for p in with_engine.equity_curve]

    snapshot = engine.last_snapshot
    assert snapshot.risk_equity > 0.0
    assert snapshot.basis_counts[ValuationBasis.MARKED] == len(snapshot.symbols)


# ----- Starting from what the account actually holds ----- #

_ADOPT_T0 = datetime(2026, 7, 1, 14, 30)


def test_a_portfolio_can_start_from_real_cash_and_positions():
    portfolio = BasicPortfolio(init_capital=10_000.0)

    portfolio.adopt(25_000.0, {"AAPL": (10.0, 99.0)}, time_stamp=_ADOPT_T0)

    assert portfolio.cash_balance == 25_000.0
    assert portfolio.open_qty_by_symbol == {"AAPL": 10.0}
    assert portfolio.open_cost_by_symbol["AAPL"] == pytest.approx(990.0)
    # Equity, peak and the drawdown baseline all start from the real account.
    assert portfolio.current_equity == pytest.approx(25_990.0)
    assert portfolio.peak_equity == pytest.approx(25_990.0)


def test_an_adopted_position_can_be_sold():
    portfolio = BasicPortfolio(init_capital=0.0)
    portfolio.adopt(1_000.0, {"AAPL": (10.0, 99.0)}, time_stamp=_ADOPT_T0)

    asyncio.run(portfolio.process_fill(Fill(
        time_stamp=_ADOPT_T0, symbol="AAPL", side=SignalType.SELL, quantity=10.0, price=101.0,
    )))

    assert portfolio.open_qty_by_symbol.get("AAPL", 0.0) == 0.0
    assert portfolio.realised_pnl == pytest.approx(20.0)


def test_adopting_after_trading_has_started_is_refused():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    asyncio.run(portfolio.process_fill(Fill(
        time_stamp=_ADOPT_T0, symbol="AAPL", side=SignalType.BUY, quantity=1.0, price=100.0,
    )))

    with pytest.raises(RuntimeError):
        portfolio.adopt(25_000.0)


# ----- An execution is applied once ----- #

def test_the_same_execution_delivered_twice_is_applied_once():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    fill = Fill(
        time_stamp=_ADOPT_T0, symbol="AAPL", side=SignalType.BUY, quantity=5.0, price=100.0,
        execution_id="e1",
    )

    asyncio.run(portfolio.process_fill(fill))
    asyncio.run(portfolio.process_fill(fill))

    assert portfolio.open_qty_by_symbol["AAPL"] == 5.0
    assert portfolio.cash_balance == pytest.approx(9_500.0)


def test_fills_without_an_execution_id_are_still_applied():
    portfolio = BasicPortfolio(init_capital=10_000.0)
    fill = Fill(time_stamp=_ADOPT_T0, symbol="AAPL", side=SignalType.BUY, quantity=5.0, price=100.0)

    asyncio.run(portfolio.process_fill(fill))
    asyncio.run(portfolio.process_fill(fill))

    # A simulated broker has no execution ids and must keep working unchanged.
    assert portfolio.open_qty_by_symbol["AAPL"] == 10.0
