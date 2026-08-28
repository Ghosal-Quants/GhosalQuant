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
from ghosalquant.core.contracts import Contract, Right
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
    strategy, portfolio, _ = run(target=99.0, stop=-0.10)

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
        target=99.0, stop=-0.99, max_hold_minutes=100_000, close_before_expiry=False,
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
