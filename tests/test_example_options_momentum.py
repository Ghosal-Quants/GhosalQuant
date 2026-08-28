import pytest

from examples.example_options_momentum import (
    CHAIN_WIDTH,
    MULTIPLIER,
    UNDERLIER,
    build_chain,
    build_events,
    occ_symbol,
    run,
)
from ghosalquant.core.contracts import Contract, ContractRegistry, Right
from ghosalquant.core.types import Bar, OrderBook, SignalType


def _quoted_asks(events) -> dict[tuple[str, object], float]:
    return {
        (event.symbol, event.time_stamp): event.best_ask.price
        for event in events
        if isinstance(event, OrderBook)
    }


def test_the_chain_is_written_in_occ_and_reads_back_the_same():
    chain = build_chain()

    assert len(chain) == len({contract.symbol for contract in chain})
    for contract in chain:
        assert len(contract.symbol) == 21
        reread = Contract.from_occ(contract.symbol, multiplier=MULTIPLIER)
        assert (reread.underlier, reread.right, reread.strike, reread.expiry) == (
            UNDERLIER, contract.right, contract.strike, contract.expiry
        )
    assert occ_symbol(UNDERLIER, chain[0].expiry, Right.PUT, 7.5).endswith("P00007500")


def test_only_the_strikes_near_the_money_are_quoted():
    events = build_events()
    bars = [event for event in events if isinstance(event, Bar)]
    books = [event for event in events if isinstance(event, OrderBook)]

    assert all(bar.symbol == UNDERLIER for bar in bars)
    assert all(book.is_two_sided() and not book.is_crossed() for book in books)
    assert all(
        earlier.time_stamp <= later.time_stamp for earlier, later in zip(events, events[1:])
    )

    spot_at = {bar.time_stamp: bar.close for bar in bars}
    strike_of = {contract.symbol: contract.strike for contract in build_chain()}
    assert all(
        abs(strike_of[book.symbol] - spot_at[book.time_stamp]) <= CHAIN_WIDTH for book in books
    )


def test_it_buys_the_direction_the_underlier_moved():
    strategy, portfolio, _ = run()
    contracts = {c.symbol: c for c in build_chain()}
    bought = [fill for fill in strategy.fills if fill.side is SignalType.BUY]

    assert [contracts[fill.symbol].right for fill in bought] == [Right.PUT, Right.CALL]
    refused = strategy.skipped_on_spread + strategy.skipped_on_premium
    assert refused > 0, "the far wings should have been refused as too wide or too cheap"
    assert portfolio.open_qty_by_symbol == {}


def test_every_order_is_priced_from_its_own_contracts_live_quote():
    """
    The trap this strategy is built around.

    Choosing a contract on one event and pricing it from the last quote of another sends a limit
    to where the market was, not where it is. In a fast move that never fills, and the whole
    signal is lost silently.
    """
    strategy, _, _ = run()
    asks = _quoted_asks(build_events())

    bought = [fill for fill in strategy.fills if fill.side is SignalType.BUY]
    assert bought, "nothing was bought, so nothing was proved"
    for fill in bought:
        assert fill.price == pytest.approx(asks[(fill.symbol, fill.time_stamp)])


def test_the_round_trips_reconcile_with_the_multiplier():
    strategy, portfolio, kpms = run()

    legs = strategy.fills
    expected = sum(
        (sell.price - buy.price) * buy.quantity * MULTIPLIER
        for buy, sell in zip(legs[::2], legs[1::2])
    )
    assert portfolio.realised_pnl == pytest.approx(expected)
    assert kpms.total_pnl == pytest.approx(expected)
    assert kpms.total_trades == len(list(portfolio.closed_trades)) == 2


def test_a_stop_closes_the_trade_that_goes_the_wrong_way():
    """The rally fades, so a trade held through it has to be stopped out rather than run."""
    # No Kelly: an unreachable target disables the target exit, and the sizer refuses it.
    strategy, portfolio, _ = run(target=99.0, stop=-0.10, kelly=None)

    sold = [fill for fill in strategy.fills if fill.side is SignalType.SELL]
    buys = [fill for fill in strategy.fills if fill.side is SignalType.BUY]
    losing = [
        sell for buy, sell in zip(buys, sold) if sell.price < buy.price * 0.95
    ]
    assert losing, "the faded rally should have hit its stop"
    assert portfolio.open_qty_by_symbol == {}


def test_a_long_put_held_to_expiry_is_exercised_into_short_stock():
    """
    Exercise, end to end: the option closes at nothing and the stock trades at the strike.

    Nobody held the shares to deliver, so exercising leaves the account short them — which is the
    signed position, the multiplier and settlement all having to agree at once.
    """
    strategy, portfolio, _ = run(
        target=99.0, stop=-0.99, max_hold_minutes=100_000, close_before_expiry=False, kelly=None,
        # Reach for the expiring contract: cheap and wide, which the live strategy declines.
        min_days_to_expiry=0, max_spread=0.6, min_premium_fraction=0.0,
    )

    put = strategy.fills[0]
    contract = Contract.from_occ(put.symbol, multiplier=MULTIPLIER)
    assert contract.right is Right.PUT

    assert portfolio.open_qty_by_symbol.get(put.symbol, 0.0) == 0.0
    assert portfolio.open_qty_by_symbol[UNDERLIER] == pytest.approx(
        -put.quantity * MULTIPLIER
    )
    # The premium is the whole loss on the option leg; the stock leg opened at the strike.
    assert portfolio.realised_pnl == pytest.approx(-put.price * put.quantity * MULTIPLIER)
    assert portfolio.average_entry_price(UNDERLIER) == pytest.approx(contract.strike)


def test_with_no_edge_assumed_it_does_not_trade_at_all():
    """
    The Kelly sizer's whole point: a fairly priced option is not a bet worth making.

    ``continuation`` is the strategy's belief that the move carries on. Set it to zero and the
    only thing left is a fairly priced option, so there is no edge and nothing should be bought.
    """
    strategy, portfolio, kpms = run(kelly=0.25, continuation=0.0)

    assert strategy.fills == []
    assert portfolio.open_qty_by_symbol == {}
    assert kpms.total_trades == 0


def test_a_stronger_belief_in_the_move_buys_more_of_it():
    timid, _, _ = run(kelly=0.25, continuation=0.4)
    bold, _, _ = run(kelly=0.25, continuation=0.8)

    first_timid = next(f for f in timid.fills if f.side is SignalType.BUY)
    first_bold = next(f for f in bold.fills if f.side is SignalType.BUY)
    assert first_bold.quantity > first_timid.quantity


def test_the_bar_interval_is_measured_rather_than_assumed():
    """
    The hold is converted to bars to scale volatility, so the bar size has to be the real one.

    Assuming the fixture's five minutes made the estimate meaningless on daily bars: sigma came
    out seventeen times too large, which pins the probability at a half whatever the drift.
    """
    from datetime import datetime, timedelta, timezone

    from examples.example_options_momentum import BAR_MINUTES, MomentumOptionBuyer
    from ghosalquant.core.types import Bar

    strategy = MomentumOptionBuyer(ContractRegistry(build_chain()))
    assert strategy._bar_seconds == BAR_MINUTES * 60.0, "starts at the configured default"

    at = datetime(2026, 3, 2, 14, 30, tzinfo=timezone.utc)
    for day in range(3):
        when = at + timedelta(days=day)
        strategy.on_market_data(Bar(time_stamp=when, symbol=UNDERLIER, open=150.0, high=150.0,
                                    low=150.0, close=150.0, volume=1.0))
    assert strategy._bar_seconds == 86_400.0, "daily bars, measured from the stream"
