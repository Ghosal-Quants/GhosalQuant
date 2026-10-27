import asyncio
import logging
from datetime import datetime

import pytest

from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.contracts import Contract, ContractRegistry
from ghosalquant.core.types import (
    BookLevel,
    Liquidity,
    OpenOrderView,
    Order,
    OrderBook,
    Signal,
    SignalType,
)
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.risk.sizing import kelly_fraction
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk import (
    FractionalKellySizer,
    CashAllocationConfig,
    CashAllocationRule,
    ConfidenceSizer,
    CorrelationFilter,
    IntegerQuantity,
    MaxOpenPositions,
    MaxSymbolExposure,
    OrderDraft,
    RiskContext,
    RiskManager,
    SignalQuantityCap,
    SymbolCapitalFractionSizer,
    TickRounding,
    Window,
    plan_cash_reclamation,
    resting_buy_notional,
    sizing_capital,
)
from ghosalquant.util.rounding import ceil_to_tick, floor_to_tick, round_to_tick

T0 = datetime(2026, 4, 1, 14, 30)


def draft(
    *, symbol: str = "AAPL", side: SignalType = SignalType.BUY, quantity: float | None = 10.0,
    price: float = 100.0, **metadata,
) -> OrderDraft:
    return OrderDraft(
        time_stamp=T0, symbol=symbol, side=side, quantity=quantity, price=price,
        metadata=dict(metadata),
    )


def resting(
    symbol: str, quantity: float, limit_price: float, *, slot: str | None = None, **metadata
) -> OpenOrderView:
    order = Order(
        time_stamp=T0, symbol=symbol, side=SignalType.BUY, quantity=quantity, ref_price=limit_price,
        metadata={metadata_keys.ORDER_SLOT: slot, **metadata} if slot else dict(metadata),
        limit_price=limit_price,
    )
    return OpenOrderView(
        broker_order_id=slot or "id", order=order, remaining_quantity=quantity,
        submitted_at=T0, limit_price=limit_price,
    )


# ----- Whole units ----- #

def test_integer_quantity_rounds_down_to_whole_units():
    rule = IntegerQuantity()
    order = draft(quantity=10.9)

    assert rule.apply(order, RiskContext()) is True
    assert order.quantity == 10.0


def test_integer_quantity_respects_a_lot_size():
    rule = IntegerQuantity(lot_size=100.0)
    order = draft(quantity=250.0)

    assert rule.apply(order, RiskContext()) is True
    assert order.quantity == 200.0


def test_a_fractional_lot_size_does_not_lose_a_unit_to_floating_point():
    rule = IntegerQuantity(lot_size=0.001)
    order = draft(quantity=0.3)

    rule.apply(order, RiskContext())
    assert order.quantity == pytest.approx(0.3)


def test_a_draft_that_rounds_away_is_dropped():
    rule = IntegerQuantity()

    assert rule.apply(draft(quantity=0.4), RiskContext()) is False
    assert rule.apply(draft(quantity=None), RiskContext()) is True


# ----- The strategy's own cap ----- #

def test_a_sizer_is_held_to_the_quantity_the_signal_asked_for():
    rule = SignalQuantityCap()
    order = draft(quantity=100.0, **{metadata_keys.MAX_QUANTITY: 25.0})

    assert rule.apply(order, RiskContext()) is True
    assert order.quantity == 25.0


def test_a_signal_without_a_cap_is_left_alone_and_a_zero_cap_blocks():
    rule = SignalQuantityCap()
    uncapped = draft(quantity=100.0)

    assert rule.apply(uncapped, RiskContext()) is True
    assert uncapped.quantity == 100.0
    assert rule.apply(draft(**{metadata_keys.MAX_QUANTITY: 0.0}), RiskContext()) is False


# ----- Sizing capital ----- #

def test_sizing_capital_is_fixed_until_a_reference_is_given():
    context = RiskContext(current_equity=8_000.0)

    assert sizing_capital(context, capital=10_000.0) == 10_000.0
    assert sizing_capital(context, capital=10_000.0, reference_capital=10_000.0) == 8_000.0


def test_sizing_capital_falls_back_to_peak_and_drawdown():
    context = RiskContext(peak_equity=10_000.0, current_drawdown=-0.2)

    assert sizing_capital(context, capital=10_000.0, reference_capital=10_000.0) == pytest.approx(8_000.0)


def test_the_execution_system_tells_risk_rules_what_equity_is():
    seen: list[RiskContext] = []

    class Recorder:
        def apply(self, _draft: OrderDraft, context: RiskContext) -> bool:
            seen.append(context)
            return True

    class Quoter(OrderBookStrategy):
        def on_start(self): ...

        def on_end(self): ...

        def on_order_book(self, book, context=None):
            return Signal(
                time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
                ref_price=book.best_bid.price, confidence=1.0, quantity=1.0,
                limit_price=book.best_bid.price,
            )

    book = OrderBook(
        time_stamp=T0, symbol="AAPL",
        bids=(BookLevel(100.0, 500.0),), asks=(BookLevel(100.02, 500.0),),
    )
    asyncio.run(ExecutionSystem(
        ListDataSource([book]), Quoter(),
        portfolio=BasicPortfolio(init_capital=50_000.0),
        risk_manager=RiskManager(rules=[Recorder()]),
        broker=OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=1),
    ).run())

    assert seen and seen[0].current_equity == pytest.approx(50_000.0)


# ----- Exposure is measured, not counted ----- #

def test_exposure_is_capped_by_notional_where_a_position_count_would_not_be():
    rule = MaxSymbolExposure(capital=10_000.0, max_notional_fraction=0.2)
    order = draft(quantity=100.0, price=100.0)

    # 20% of 10,000 is 2,000, so 20 shares at 100 rather than the 100 requested.
    assert rule.apply(order, RiskContext()) is True
    assert order.quantity == pytest.approx(20.0)


def test_what_is_already_held_counts_against_the_ceiling():
    rule = MaxSymbolExposure(capital=10_000.0, max_notional_fraction=0.2)
    context = RiskContext(
        open_qty_by_symbol={"AAPL": 15.0}, position_cost_by_symbol={"AAPL": 1_500.0}
    )
    order = draft(quantity=100.0)

    assert rule.apply(order, context) is True
    assert order.quantity == pytest.approx(5.0)


def test_a_resting_bid_is_capital_already_committed():
    rule = MaxSymbolExposure(capital=10_000.0, max_notional_fraction=0.2)
    context = RiskContext(open_orders=(resting("AAPL", 12.0, 100.0),))
    order = draft(quantity=100.0)

    assert rule.apply(order, context) is True
    assert order.quantity == pytest.approx(8.0)


def test_a_full_symbol_blocks_and_a_sell_is_never_capped():
    rule = MaxSymbolExposure(capital=10_000.0, max_notional_fraction=0.2)
    full = RiskContext(open_qty_by_symbol={"AAPL": 25.0}, position_cost_by_symbol={"AAPL": 2_500.0})

    assert rule.apply(draft(quantity=1.0), full) is False
    sell = draft(side=SignalType.SELL, quantity=25.0)
    assert rule.apply(sell, full) is True
    assert sell.quantity == 25.0


def test_a_takers_fee_is_charged_into_the_ceiling():
    rule = MaxSymbolExposure(capital=10_000.0, max_notional_fraction=0.2, taker_fee_bps=100.0)
    order = draft(quantity=100.0, price=100.0)
    order.liquidity = Liquidity.TAKER

    # A 1% fee makes each share cost 101, so 19 fit under 2,000 rather than 20.
    assert rule.apply(order, RiskContext()) is True
    assert order.quantity == pytest.approx(2_000.0 / 101.0)


# ----- Sizing by money, not by count ----- #

def test_a_notional_sizer_spends_the_same_money_on_any_price():
    rule = SymbolCapitalFractionSizer(capital=10_000.0, default_max_notional_fraction=0.1)
    cheap, dear = draft(price=5.0), draft(symbol="BRK", price=500.0)

    rule.apply(cheap, RiskContext())
    rule.apply(dear, RiskContext())

    assert cheap.quantity * 5.0 == pytest.approx(1_000.0)
    assert dear.quantity * 500.0 == pytest.approx(1_000.0)


def test_a_notional_sizer_tops_up_towards_the_budget_rather_than_adding_to_it():
    rule = SymbolCapitalFractionSizer(capital=10_000.0, default_max_notional_fraction=0.1)
    context = RiskContext(
        open_qty_by_symbol={"AAPL": 6.0}, position_cost_by_symbol={"AAPL": 600.0}
    )
    order = draft(price=100.0)

    assert rule.apply(order, context) is True
    assert order.quantity == pytest.approx(4.0)


def test_a_notional_sizer_sells_what_is_held_and_scales_a_buy_by_confidence():
    rule = SymbolCapitalFractionSizer(capital=10_000.0, default_max_notional_fraction=0.1)
    held = RiskContext(open_qty_by_symbol={"AAPL": 7.0})

    sell = draft(side=SignalType.SELL, quantity=None)
    assert rule.apply(sell, held) is True
    assert sell.quantity == 7.0

    half = draft(**{metadata_keys.CONFIDENCE: 0.5})
    rule.apply(half, RiskContext())
    assert half.quantity == pytest.approx(5.0)


def test_a_symbol_with_no_budget_is_refused():
    rule = SymbolCapitalFractionSizer(
        capital=10_000.0, max_notional_fraction_by_symbol={"AAPL": 0.1}
    )

    assert rule.apply(draft(symbol="MSFT"), RiskContext()) is False


# ----- Dry powder ----- #

def test_resting_buys_are_measured_by_symbol_and_bucket():
    orders = (
        resting("AAPL", 10.0, 100.0, **{metadata_keys.ALLOCATION_BUCKET: "ladder"}),
        resting("MSFT", 5.0, 50.0),
    )

    assert resting_buy_notional(orders) == pytest.approx(1_250.0)
    assert resting_buy_notional(orders, symbol="AAPL") == pytest.approx(1_000.0)
    assert resting_buy_notional(orders, bucket="ladder") == pytest.approx(1_000.0)


def test_an_order_is_trimmed_to_the_tightest_cash_ceiling():
    rule = CashAllocationRule(
        CashAllocationConfig(total_fraction=0.9, max_symbol_fraction=0.2), capital=10_000.0
    )
    order = draft(quantity=100.0, price=100.0)

    # The symbol ceiling of 2,000 binds before the 9,000 account ceiling.
    assert rule.apply(order, RiskContext()) is True
    assert order.quantity == pytest.approx(20.0)


def test_a_bucket_keeps_a_ladder_from_spending_the_whole_account():
    rule = CashAllocationRule(
        CashAllocationConfig(
            total_fraction=1.0, max_symbol_fraction=1.0, bucket_fractions={"ladder": 0.25}
        ),
        capital=10_000.0,
    )
    order = draft(quantity=100.0, **{metadata_keys.ALLOCATION_BUCKET: "ladder"})
    context = RiskContext(
        open_orders=(resting("AAPL", 20.0, 100.0, **{metadata_keys.ALLOCATION_BUCKET: "ladder"}),)
    )

    assert rule.apply(order, context) is True
    assert order.quantity == pytest.approx(5.0)


def test_an_order_with_no_headroom_left_is_blocked():
    rule = CashAllocationRule(
        CashAllocationConfig(total_fraction=0.5, max_symbol_fraction=1.0), capital=10_000.0
    )
    context = RiskContext(open_orders=(resting("MSFT", 50.0, 100.0),))

    assert rule.apply(draft(quantity=10.0), context) is False


def test_a_sell_is_outside_the_cash_policy():
    rule = CashAllocationRule(CashAllocationConfig(total_fraction=0.01), capital=10_000.0)
    sell = draft(side=SignalType.SELL, quantity=50.0)

    assert rule.apply(sell, RiskContext()) is True
    assert sell.quantity == 50.0


# ----- Funding a better order ----- #

def test_reclamation_cancels_the_weakest_orders_until_enough_is_freed():
    orders = (
        resting("AAPL", 5.0, 100.0, slot="weak"),
        resting("AAPL", 5.0, 100.0, slot="middling"),
        resting("AAPL", 5.0, 100.0, slot="strong"),
    )
    strengths = {"weak": 1.0, "middling": 2.0, "strong": 9.0}

    plan = plan_cash_reclamation(
        required_notional=900.0, open_orders=orders, symbol="AAPL", candidate_strength=5.0,
        strength=lambda view: strengths[view.order.metadata[metadata_keys.ORDER_SLOT]],
    )

    # "strong" outranks the candidate, so only the two weaker slots are offered.
    assert plan.order_slots == ("weak", "middling")
    assert plan.reclaimed_notional == pytest.approx(1_000.0)


def test_reclamation_gives_up_when_the_weaker_orders_cannot_fund_it():
    orders = (resting("AAPL", 1.0, 100.0, slot="weak"),)

    plan = plan_cash_reclamation(
        required_notional=5_000.0, open_orders=orders, symbol="AAPL", candidate_strength=5.0,
        strength=lambda _view: 1.0,
    )

    assert plan.order_slots == ()
    assert plan.reclaimed_notional == 0.0


# ----- Rules report through logging ----- #

def test_a_blocked_order_is_logged_not_printed(capsys, caplog):
    rules = [MaxOpenPositions(max_open_positions=1), ConfidenceSizer(min_quantity=0.0, max_quantity=1.0)]
    context = RiskContext(open_positions=3)

    with caplog.at_level(logging.INFO):
        assert rules[0].apply(draft(), context) is False
        assert rules[1].apply(draft(**{metadata_keys.CONFIDENCE: 0.0}), context) is False

    assert capsys.readouterr().out == ""
    assert "MaxOpenPositions blocked" in caplog.text
    assert "ConfidenceSizer blocked" in caplog.text


# ----- Prices the venue will accept ----- #

def test_rounding_to_a_tick_beats_float_rounding():
    # round() works on a binary float that cannot hold 193.375, so it rounds the wrong way.
    assert round(193.375, 2) == 193.38
    assert round_to_tick(193.375, 0.01) == 193.38
    assert round_to_tick(0.125, 0.01) == 0.13
    assert round(0.125, 2) == 0.12


def test_a_tick_can_be_any_increment():
    assert floor_to_tick(193.375, 0.05) == 193.35
    assert ceil_to_tick(193.375, 0.05) == 193.40
    assert floor_to_tick(1_234.0, 25.0) == 1_225.0

    with pytest.raises(ValueError):
        floor_to_tick(1.0, 0.0)


def test_rounding_a_limit_can_only_move_it_in_the_traders_favour():
    rule = TickRounding(tick_size=0.01)
    buy = draft(price=193.375)
    buy.limit_price = 193.375
    sell = draft(side=SignalType.SELL, price=193.375)
    sell.limit_price = 193.375

    assert rule.apply(buy, RiskContext()) is True
    assert rule.apply(sell, RiskContext()) is True

    # A buy never pays more than it meant to; a sell never accepts less.
    assert buy.limit_price == 193.37
    assert sell.limit_price == 193.38


def test_a_market_order_is_left_alone():
    rule = TickRounding()
    market = draft()
    market.limit_price = None

    assert rule.apply(market, RiskContext()) is True
    assert market.limit_price is None


def test_a_per_symbol_tick_overrides_the_default():
    rule = TickRounding(tick_size=0.01, tick_size_by_symbol={"BRK": 0.05})
    penny, nickel = draft(symbol="AAPL", price=10.0), draft(symbol="BRK", price=10.0)
    penny.limit_price = 10.024
    nickel.limit_price = 10.024

    rule.apply(penny, RiskContext())
    rule.apply(nickel, RiskContext())

    assert penny.limit_price == 10.02
    assert nickel.limit_price == 10.00


def test_the_metadata_copy_of_the_limit_is_kept_in_step():
    rule = TickRounding()
    order = draft(**{metadata_keys.LIMIT_PRICE: 193.375})
    order.limit_price = 193.375

    rule.apply(order, RiskContext())

    assert order.metadata[metadata_keys.LIMIT_PRICE] == 193.37


def test_a_price_that_rounds_away_to_nothing_is_dropped():
    rule = TickRounding(tick_size=0.01)
    dust = draft(price=0.004)
    dust.limit_price = 0.004

    assert rule.apply(dust, RiskContext()) is False


def test_a_sized_order_comes_out_tradable():
    """The whole point: capital sizing and a book mid produce something a venue accepts."""
    rules = [
        SymbolCapitalFractionSizer(capital=50_000.0, default_max_notional_fraction=0.07),
        TickRounding(tick_size=0.01),
        IntegerQuantity(),
    ]
    order = draft(price=193.37)
    order.limit_price = (193.37 + 193.38) / 2  # straight off the book mid

    for rule in rules:
        assert rule.apply(order, RiskContext()) is True

    assert order.quantity == int(order.quantity)
    assert order.limit_price == 193.37


# ----- A rolling window says when it has nothing to say ----- #

def test_a_window_has_no_answer_until_it_has_filled():
    window = Window(60)

    assert window.required == 60
    assert window.of([1.0, 2.0, 3.0]) is None
    assert window.of(None) is None
    assert len(window.of([float(i) for i in range(60)])) == 60


def test_a_window_keeps_only_its_own_size_once_past_it():
    window = Window(5)

    assert window.of([float(i) for i in range(20)]) == [15.0, 16.0, 17.0, 18.0, 19.0]


def test_a_ramp_up_buys_an_earlier_but_weaker_answer():
    window = Window(60, ramp_up=10)

    assert window.required == 10
    assert window.of([float(i) for i in range(9)]) is None
    assert len(window.of([float(i) for i in range(10)])) == 10


def test_a_window_that_cannot_mean_anything_is_refused():
    with pytest.raises(ValueError):
        Window(0)
    with pytest.raises(ValueError):
        Window(10, ramp_up=0)
    with pytest.raises(ValueError):
        Window(10, ramp_up=11)


# ----- The correlation filter waits for its window ----- #

def _correlated_history(observations: int) -> dict[str, list[float]]:
    """Two series that move together, so a full window is unambiguously correlated."""
    return {
        "NEW": [100.0 + index for index in range(observations)],
        "AAPL": [50.0 + index * 0.5 for index in range(observations)],
    }


def _correlation_blocked(rule: CorrelationFilter, observations: int) -> bool:
    context = RiskContext(
        open_qty_by_symbol={"AAPL": 1.0}, price_history=_correlated_history(observations)
    )
    return not rule.apply(draft(symbol="NEW"), context)


def test_a_correlation_is_not_acted_on_before_its_window_has_filled():
    rule = CorrelationFilter(lookback=60, threshold=0.75)

    # Three observations is noise. Acting on it blocks or allows at random.
    assert _correlation_blocked(rule, 3) is False
    assert _correlation_blocked(rule, 59) is False
    assert _correlation_blocked(rule, 60) is True


def test_an_explicit_ramp_up_lets_the_filter_act_sooner():
    rule = CorrelationFilter(lookback=60, threshold=0.75, ramp_up=10)

    assert _correlation_blocked(rule, 9) is False
    assert _correlation_blocked(rule, 10) is True


def test_a_held_symbol_without_enough_history_is_passed_over():
    rule = CorrelationFilter(lookback=10, threshold=0.75)
    history = _correlated_history(10)
    history["THIN"] = [10.0, 11.0, 12.0]
    context = RiskContext(
        open_qty_by_symbol={"THIN": 1.0}, price_history=history
    )

    # THIN has three observations, too few to correlate on.
    assert rule.apply(draft(symbol="NEW"), context) is True


def test_an_uncorrelated_symbol_is_left_alone():
    rule = CorrelationFilter(lookback=10, threshold=0.75)
    history = _correlated_history(10)
    history["AAPL"] = [50.0, 55.0, 51.0, 58.0, 49.0, 60.0, 48.0, 62.0, 47.0, 64.0]
    context = RiskContext(open_qty_by_symbol={"AAPL": 1.0}, price_history=history)

    assert rule.apply(draft(symbol="NEW"), context) is True


def test_a_sell_and_a_flat_book_are_outside_the_filter():
    rule = CorrelationFilter(lookback=10, threshold=0.75)
    context = RiskContext(
        open_qty_by_symbol={"AAPL": 1.0}, price_history=_correlated_history(10)
    )

    assert rule.apply(draft(symbol="NEW", side=SignalType.SELL), context) is True
    assert rule.apply(draft(symbol="NEW"), RiskContext(price_history=_correlated_history(10))) is True


# ----- Sizing by the edge ----- #

OPTION = Contract.from_occ("AAPL  260116C00150000")
CHAIN = ContractRegistry([OPTION])


def _edge(probability: float, gain: float = 0.5, loss: float = 0.35, **kwargs):
    settings = dict(quantity=None, price=5.0, symbol=OPTION.symbol)
    settings.update(kwargs)
    return draft(
        **settings,
        **{metadata_keys.CONFIDENCE: probability, metadata_keys.TAKE_PROFIT: gain,
           metadata_keys.STOP_LOSS: loss},
    )


def test_kelly_fraction_matches_the_classical_result():
    # An even-money bet is the textbook 2p-1, and a fair one is no bet at all.
    assert kelly_fraction(0.6, 1.0, 1.0) == pytest.approx(0.2)
    assert kelly_fraction(0.5, 1.0, 1.0) == 0.0

    # Asymmetric: +50% against -35% needs better than a 41.2% strike rate to be worth anything.
    assert kelly_fraction(0.42, 0.5, 0.35) > 0.0
    assert kelly_fraction(0.40, 0.5, 0.35) == 0.0

    for bad in ((1.5, 0.5, 0.35), (0.5, 0.0, 0.35), (0.5, 0.5, 0.0), (0.5, 0.5, 1.5)):
        with pytest.raises(ValueError):
            kelly_fraction(*bad)


def test_the_kelly_sizer_refuses_a_bet_with_no_edge():
    rule = FractionalKellySizer(capital=100_000.0)
    losing = _edge(0.35)

    assert rule.apply(losing, RiskContext(current_equity=100_000.0, contracts=CHAIN)) is False
    assert losing.metadata["kelly_full_fraction"] == 0.0


def test_it_stakes_a_fraction_of_what_kelly_asks_for():
    """Kelly is optimal only when its inputs are known; here they are estimated."""
    context = RiskContext(current_equity=100_000.0, contracts=CHAIN)
    full = FractionalKellySizer(capital=100_000.0, fraction=1.0, max_notional_fraction=1.0)
    quarter = FractionalKellySizer(capital=100_000.0, fraction=0.25, max_notional_fraction=1.0)

    whole, part = _edge(0.5), _edge(0.5)
    assert full.apply(whole, context) and quarter.apply(part, context)

    assert part.metadata["kelly_staked_fraction"] == pytest.approx(
        whole.metadata["kelly_staked_fraction"] * 0.25
    )
    assert part.quantity == pytest.approx(whole.quantity * 0.25)


def test_a_confident_signal_is_still_capped():
    """A probability estimate near certainty is the least trustworthy input of the three."""
    sized = _edge(0.95)
    rule = FractionalKellySizer(capital=100_000.0, fraction=1.0, max_notional_fraction=0.05)

    assert rule.apply(sized, RiskContext(current_equity=100_000.0, contracts=CHAIN))
    assert sized.metadata["kelly_full_fraction"] > 0.05
    assert sized.metadata["kelly_staked_fraction"] == pytest.approx(0.05)
    # 5% of 100,000 is 5,000, which buys ten contracts at $5.00 with a hundred-share multiplier.
    assert sized.quantity == pytest.approx(10.0)


def test_a_signal_that_offers_no_estimate_is_left_to_other_rules():
    rule = FractionalKellySizer(capital=100_000.0)
    unsized = draft(quantity=7.0)

    assert rule.apply(unsized, RiskContext(current_equity=100_000.0, contracts=CHAIN)) is True
    assert unsized.quantity == 7.0


def test_sells_are_not_sized_by_it():
    rule = FractionalKellySizer(capital=100_000.0)
    exit_order = _edge(0.9, side=SignalType.SELL, quantity=3.0)

    assert rule.apply(exit_order, RiskContext(current_equity=100_000.0, contracts=CHAIN)) is True
    assert exit_order.quantity == 3.0


# ----- Sizing to a target weight ----- #

def _weighted(
    weight: float, side: SignalType = SignalType.BUY, *, symbol: str = "AAPL", price: float = 100.0
) -> Signal:
    return Signal(
        time_stamp=T0, symbol=symbol, signal_type=side, ref_price=price,
        metadata={metadata_keys.TARGET_WEIGHT: weight},
    )


def test_a_target_weight_becomes_the_trade_that_reaches_it():
    manager = RiskManager()

    entry = manager.evaluate(_weighted(0.5), RiskContext(current_equity=10_000.0))
    assert entry is not None
    assert entry.side is SignalType.BUY
    assert entry.quantity == 50.0

    trim = manager.evaluate(
        _weighted(0.2, SignalType.SELL),
        RiskContext(current_equity=10_000.0, open_qty_by_symbol={"AAPL": 50.0}, open_positions=1),
    )
    assert trim is not None
    assert trim.side is SignalType.SELL
    assert trim.quantity == 30.0


def test_a_target_weight_prices_a_contract_at_its_multiplier():
    order = RiskManager().evaluate(
        _weighted(0.5, symbol=OPTION.symbol, price=5.0),
        RiskContext(current_equity=10_000.0, contracts=CHAIN),
    )

    # $5,000 is ten contracts at $5.00 with a hundred-share multiplier.
    assert order is not None
    assert order.quantity == 10.0


def test_a_target_weight_counts_orders_still_resting():
    context = RiskContext(
        current_equity=10_000.0,
        open_qty_by_symbol={"AAPL": 20.0},
        open_orders=(resting("AAPL", 30.0, 100.0),),
    )

    # 20 held plus 30 resting already make the 50 shares a half weight asks for.
    assert RiskManager().evaluate(_weighted(0.5), context) is None
    order = RiskManager().evaluate(_weighted(0.6), context)
    assert order is not None
    assert order.quantity == pytest.approx(10.0)


def test_a_target_weight_without_equity_is_dropped():
    assert RiskManager().evaluate(_weighted(0.5), RiskContext()) is None
