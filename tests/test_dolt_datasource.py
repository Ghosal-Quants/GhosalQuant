import asyncio
from datetime import date

import pytest

from ghosalquant.core.contracts import Contract, Right
from ghosalquant.core.types import Bar, OrderBook
from ghosalquant.data_layer.dolt_datasource import DoltOptionsDataSource


def _row(d, expiry, strike, cp, bid, ask, symbol="SPY"):
    return {
        "date": d, "act_symbol": symbol, "expiration": expiry, "strike": f"{strike:.2f}",
        "call_put": cp, "bid": f"{bid:.2f}", "ask": f"{ask:.2f}", "vol": "0.1230",
        "delta": "0.5330",
    }


ROWS = [
    _row("2024-06-03", "2024-06-17", 528.0, "Call", 5.51, 5.55),
    _row("2024-06-03", "2024-06-17", 528.0, "Put", 4.54, 4.57),
    _row("2024-06-03", "2024-06-17", 560.0, "Call", 0.12, 0.15),
    _row("2024-06-03", "2024-06-28", 528.0, "Call", 6.70, 6.73),
    _row("2024-06-04", "2024-06-17", 530.0, "Call", 4.90, 4.95),
    _row("2024-06-04", "2024-06-17", 530.0, "Put", 5.10, 5.15),
]


def _source(rows=ROWS, **kwargs):
    settings = dict(symbols=["SPY"])
    settings.update(kwargs)
    source = DoltOptionsDataSource("unused", **settings)
    source._rows = lambda: rows
    return source


def _drain(source):
    async def go():
        return [e async for e in source]
    return asyncio.run(go())


def test_quotes_come_through_as_real_two_sided_books():
    events = _drain(_source(include_underlier=False))

    assert events and all(isinstance(e, OrderBook) for e in events)
    assert all(e.is_two_sided() and not e.is_crossed() for e in events)
    call = next(e for e in events if "240617C00528000" in e.symbol)
    assert (call.best_bid.price, call.best_ask.price) == (5.51, 5.55)


def test_contracts_register_in_the_occ_form():
    source = _source()
    _drain(source)

    symbol = source.option_symbol("SPY", date(2024, 6, 17), Right.CALL, 528.0)
    contract = source.contracts.get(symbol)
    assert (contract.right, contract.strike, contract.underlier) == (Right.CALL, 528.0, "SPY")
    assert Contract.from_occ(symbol, multiplier=100.0).expiry == date(2024, 6, 17)


def test_the_underlier_is_recovered_from_put_call_parity():
    """
    The table has no spot, and pairing it with one from elsewhere samples a different moment.

    K + call - put at a matched strike is the forward the options are actually priced against.
    """
    events = _drain(_source())

    spot = [e for e in events if e.symbol == "SPY"]
    assert len(spot) == 2, "one a session"
    assert all(isinstance(e, Bar) for e in spot)
    assert spot[0].close == pytest.approx(528.0 + 5.53 - 4.555, abs=0.01)
    assert events[0].symbol == "SPY", "the spot leads so a strategy has one before any option"


def test_a_session_without_a_matched_pair_gets_no_underlier():
    calls_only = [r for r in ROWS if r["call_put"] == "Call"]
    events = _drain(_source(calls_only))

    assert not any(e.symbol == "SPY" for e in events)


def test_the_strike_band_is_measured_against_that_forward():
    wide = _drain(_source())
    near = _drain(_source(strike_band=10.0))

    assert any("C00560000" in e.symbol for e in wide)
    assert not any("C00560000" in e.symbol for e in near), "32 points out is beyond the band"


def test_the_query_carries_the_filters_and_quotes_the_symbols():
    source = _source(symbols=["spy", "QQQ"], start=date(2024, 1, 1), end=date(2024, 6, 30),
                     max_tenor_days=45)
    sql = source.query()

    assert "act_symbol in ('SPY', 'QQQ')" in sql
    assert "`date` >= '2024-01-01'" in sql and "`date` <= '2024-06-30'" in sql
    assert "datediff(expiration, `date`) <= 45" in sql
    # Only quotes that could actually be traded on.
    assert "bid > 0" in sql and "ask > bid" in sql


def test_a_symbol_that_is_not_a_ticker_is_refused():
    """The query is built by hand, so the one interpolated field has to be checked."""
    for bad in ("SPY'; drop table option_chain; --", "SPY OR 1=1", ""):
        with pytest.raises(ValueError):
            DoltOptionsDataSource("unused", symbols=[bad])
