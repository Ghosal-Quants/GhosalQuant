import math
from datetime import date, datetime, timedelta

import pytest

from ghosalquant.core import blackscholes
from ghosalquant.core.blackscholes import greeks, implied_volatility, price, tenor
from ghosalquant.core.contracts import Contract, ContractRegistry, Right
from ghosalquant.core.types import BookLevel, OrderBook
from ghosalquant.core.volatility import VolSurface

NOW = datetime(2026, 1, 2, 21, 0)
SPOT = 100.0


def option(right: Right, strike: float, days: int = 30) -> Contract:
    letter = "C" if right is Right.CALL else "P"
    expiry = (NOW + timedelta(days=days)).date()
    return Contract(
        symbol=f"TEST  {expiry:%y%m%d}{letter}{round(strike * 1000):08d}",
        underlier="TEST", multiplier=100.0, right=right, strike=strike, expiry=expiry,
    )


# ----- Black-Scholes ----- #

def test_a_call_and_a_put_satisfy_put_call_parity():
    """C - P = S e^-qT - K e^-rT. If this drifts, the carry is wrong somewhere."""
    call, put = option(Right.CALL, 100.0), option(Right.PUT, 100.0)
    years = tenor(call, NOW)
    rate, dividend = 0.05, 0.01

    difference = (
        price(call, SPOT, 0.25, NOW, rate=rate, dividend=dividend)
        - price(put, SPOT, 0.25, NOW, rate=rate, dividend=dividend)
    )
    assert difference == pytest.approx(
        SPOT * math.exp(-dividend * years) - 100.0 * math.exp(-rate * years)
    )


def test_an_expired_option_is_worth_its_intrinsic_value():
    expired = option(Right.CALL, 90.0, days=-1)

    assert price(expired, SPOT, 0.25, NOW) == pytest.approx(10.0)
    assert greeks(expired, SPOT, 0.25, NOW).delta == 1.0
    assert greeks(option(Right.PUT, 90.0, days=-1), SPOT, 0.25, NOW).delta == 0.0


def test_the_greeks_match_what_bumping_the_inputs_does():
    """A closed form is only worth having if it agrees with the thing it stands in for."""
    call = option(Right.CALL, 105.0)
    vol = 0.3
    analytic = greeks(call, SPOT, vol, NOW, rate=0.04)

    step = 0.01
    up = price(call, SPOT + step, vol, NOW, rate=0.04)
    down = price(call, SPOT - step, vol, NOW, rate=0.04)
    here = price(call, SPOT, vol, NOW, rate=0.04)
    assert analytic.delta == pytest.approx((up - down) / (2 * step), rel=1e-4)
    assert analytic.gamma == pytest.approx((up - 2 * here + down) / step**2, rel=1e-3)

    # Vega is quoted per vol point and theta per day, so the bumps have to match those units.
    vega = price(call, SPOT, vol + 0.005, NOW, rate=0.04) - price(call, SPOT, vol - 0.005, NOW, rate=0.04)
    assert analytic.vega == pytest.approx(vega, rel=1e-3)
    tomorrow = price(call, SPOT, vol, NOW + timedelta(days=1), rate=0.04)
    assert analytic.theta == pytest.approx(tomorrow - here, rel=5e-2)


def test_a_price_inverts_back_to_the_vol_that_made_it():
    for right in (Right.CALL, Right.PUT):
        for strike in (80.0, 100.0, 130.0):
            contract = option(right, strike)
            quoted = price(contract, SPOT, 0.32, NOW, rate=0.03)
            assert implied_volatility(quoted, contract, SPOT, NOW, rate=0.03) == pytest.approx(
                0.32, abs=1e-4
            )


def test_a_price_no_vol_can_explain_inverts_to_nothing():
    call = option(Right.CALL, 100.0)

    assert implied_volatility(0.0, call, SPOT, NOW) is None          # not a price
    assert implied_volatility(SPOT * 2, call, SPOT, NOW) is None     # above any vol
    assert implied_volatility(1.0, option(Right.CALL, 100.0, days=-1), SPOT, NOW) is None


# ----- The surface ----- #

def _chain(vols: dict[tuple[float, int], float]) -> tuple[dict[str, float], ContractRegistry]:
    quotes, contracts = {}, []
    for (strike, days), vol in vols.items():
        for right in (Right.CALL, Right.PUT):
            contract = option(right, strike, days)
            contracts.append(contract)
            quotes[contract.symbol] = price(contract, SPOT, vol, NOW)
    return quotes, ContractRegistry(contracts)


def test_a_surface_reads_back_the_smile_it_was_built_from():
    wanted = {(90.0, 30): 0.34, (100.0, 30): 0.25, (110.0, 30): 0.29}
    quotes, contracts = _chain(wanted)

    surface = VolSurface.from_quotes(quotes, contracts=contracts, spot=SPOT, at=NOW)

    assert len(surface.smiles) == 1
    for (strike, days), vol in wanted.items():
        assert surface.vol(option(Right.CALL, strike, days), SPOT, NOW) == pytest.approx(vol, abs=1e-3)
    # A call and a put on one strike are two reads of a single number, not two points.
    assert len(surface.smiles[0].moneyness) == 3


def test_between_strikes_and_beyond_them():
    quotes, contracts = _chain({(90.0, 30): 0.34, (110.0, 30): 0.26})
    surface = VolSurface.from_quotes(quotes, contracts=contracts, spot=SPOT, at=NOW)

    middle = surface.vol(option(Right.CALL, 100.0, 30), SPOT, NOW)
    assert 0.26 < middle < 0.34
    # Held flat outside the quoted strikes: a fitted smile says nothing about what was not quoted.
    assert surface.vol(option(Right.CALL, 50.0, 30), SPOT, NOW) == pytest.approx(0.34, abs=1e-3)
    assert surface.vol(option(Right.CALL, 200.0, 30), SPOT, NOW) == pytest.approx(0.26, abs=1e-3)


def test_expiries_interpolate_in_total_variance():
    """
    Variance adds along the time axis; volatility does not.

    With 20% to 30 days and 30% to 120 days, the vol halfway through in *time* is not 25% — the
    variance is what is halfway, and taking the vol straight implies a negative variance for part
    of the stretch.
    """
    quotes, contracts = _chain({(100.0, 30): 0.20, (100.0, 120): 0.30})
    surface = VolSurface.from_quotes(quotes, contracts=contracts, spot=SPOT, at=NOW)

    near, far = surface.smiles[0].tenor, surface.smiles[1].tenor
    middle_tenor = (near + far) / 2.0
    variance = (0.20**2 * near + 0.30**2 * far) / 2.0
    expected = math.sqrt(variance / middle_tenor)

    contract = option(Right.CALL, 100.0, 75)
    got = surface.vol(contract, SPOT, NOW)
    assert got == pytest.approx(expected, rel=1e-2)
    assert got != pytest.approx(0.25, rel=1e-2), "a straight vol interpolation would land here"


def test_a_surface_prices_and_differentiates_a_contract_it_never_saw():
    quotes, contracts = _chain({(95.0, 30): 0.30, (105.0, 30): 0.26})
    surface = VolSurface.from_quotes(quotes, contracts=contracts, spot=SPOT, at=NOW)

    unseen = option(Right.CALL, 100.0, 30)
    assert surface.price(unseen, SPOT, NOW) == pytest.approx(
        price(unseen, SPOT, surface.vol(unseen, SPOT, NOW), NOW)
    )
    assert 0.0 < surface.greeks(unseen, SPOT, NOW).delta < 1.0


def test_an_empty_surface_answers_nothing_rather_than_guessing():
    empty = VolSurface()
    call = option(Right.CALL, 100.0)

    assert not empty
    assert empty.vol(call, SPOT, NOW) is None
    assert empty.price(call, SPOT, NOW) is None
    assert empty.greeks(call, SPOT, NOW) is None


def test_a_surface_can_be_built_from_the_books_the_system_already_gets():
    quotes, contracts = _chain({(95.0, 30): 0.30, (105.0, 30): 0.26})
    books = [
        OrderBook(time_stamp=NOW, symbol=symbol,
                  bids=(BookLevel(round(mid * 0.99, 2), 10.0),),
                  asks=(BookLevel(round(mid * 1.01, 2), 10.0),))
        for symbol, mid in quotes.items() if mid > 0.05
    ]

    surface = VolSurface.from_books(books, contracts=contracts, spot=SPOT, at=NOW)

    assert surface
    assert surface.vol(option(Right.CALL, 95.0, 30), SPOT, NOW) == pytest.approx(0.30, abs=0.02)
