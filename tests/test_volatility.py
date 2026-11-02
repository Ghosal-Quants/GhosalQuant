import math
from datetime import date, datetime, timedelta

import pytest

from ghosalquant.core import blackscholes
from ghosalquant.core.blackscholes import greeks, implied_volatility, price, tenor
from ghosalquant.core.contracts import Contract, ContractRegistry, Right
from ghosalquant.core.types import BookLevel, OrderBook
from ghosalquant.core.volatility import VolSurface
from ghosalquant.portfolio.attribution import Attribution, attribute
from ghosalquant.portfolio.hedging import hedge_quantity, net_delta, net_gamma

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


# ----- Where the money came from ----- #

def _attribute(**overrides):
    call = option(Right.CALL, 100.0, days=30)
    step = dict(
        spot_before=SPOT, spot_after=SPOT, vol_before=0.25, vol_after=0.25,
        at_before=NOW, at_after=NOW, rate=0.0, dividend=0.0,
    )
    step.update(overrides)
    step["value_before"] = price(call, step["spot_before"], step["vol_before"], step["at_before"])
    step["value_after"] = price(call, step["spot_after"], step["vol_after"], step["at_after"])
    return attribute(call, 1.0, **step)


def test_a_move_in_the_underlier_shows_up_as_delta_and_gamma():
    moved = _attribute(spot_after=SPOT + 2.0)

    assert moved.delta > 0.0
    assert moved.gamma > 0.0, "a long option is long gamma, so the move helps twice"
    assert (moved.vega, moved.theta) == (0.0, 0.0)
    assert moved.explained == pytest.approx(moved.total, rel=0.02)


def test_a_fall_in_implied_vol_shows_up_as_vega():
    cheapened = _attribute(vol_after=0.20)

    assert cheapened.vega < 0.0
    assert (cheapened.delta, cheapened.gamma) == (0.0, 0.0)
    assert cheapened.total < 0.0, "nothing moved, but the option is worth less"


def test_time_alone_shows_up_as_theta():
    """
    Theta explains a day well and a week less well, because theta itself accelerates.

    That shortfall is not lost: what a first-order term cannot account for is exactly what
    ``unexplained`` is for.
    """
    a_day = _attribute(at_after=NOW + timedelta(days=1))
    assert a_day.theta < 0.0
    assert a_day.explained == pytest.approx(a_day.total, rel=0.02)

    a_week = _attribute(at_after=NOW + timedelta(days=7))
    assert a_week.theta == pytest.approx(a_day.theta * 7, rel=1e-9), "theta is linear in time"
    assert a_week.total < a_week.theta, "but the real decay is worse than the straight line"
    assert a_week.unexplained < 0.0
    assert a_week.explained + a_week.unexplained == pytest.approx(a_week.total)


def test_the_pieces_add_up_to_what_actually_happened():
    """The whole point: explained plus unexplained is the position's real change in value."""
    everything = _attribute(spot_after=SPOT + 3.0, vol_after=0.28, at_after=NOW + timedelta(days=5))

    assert everything.total == pytest.approx(everything.explained + everything.unexplained)
    assert abs(everything.unexplained) < abs(everything.total) * 0.15
    assert (everything + everything).delta == pytest.approx(everything.delta * 2)
    assert (everything + Attribution()).total == pytest.approx(everything.total)


# ----- Hedging the direction out ----- #

def _hedge_setup():
    quotes, contracts = _chain({(95.0, 30): 0.28, (100.0, 30): 0.25, (105.0, 30): 0.27})
    surface = VolSurface.from_quotes(quotes, contracts=contracts, spot=SPOT, at=NOW)
    return surface, contracts


def test_a_long_call_is_long_the_underlier_and_a_long_put_is_short_it():
    surface, contracts = _hedge_setup()
    call, put = option(Right.CALL, 100.0), option(Right.PUT, 100.0)

    long_call = net_delta({call.symbol: 1.0}, underlier="TEST", contracts=contracts,
                          surface=surface, spot=SPOT, at=NOW)
    long_put = net_delta({put.symbol: 1.0}, underlier="TEST", contracts=contracts,
                         surface=surface, spot=SPOT, at=NOW)

    # One contract is a hundred shares, so an at-the-money call is worth about fifty of them.
    assert 30.0 < long_call < 70.0
    assert -70.0 < long_put < -30.0


def test_shares_count_as_themselves_and_other_underliers_do_not():
    surface, contracts = _hedge_setup()
    call = option(Right.CALL, 100.0)

    mixed = {call.symbol: 1.0, "TEST": -40.0, "SOMETHINGELSE": 500.0}
    only_option = net_delta({call.symbol: 1.0}, underlier="TEST", contracts=contracts,
                            surface=surface, spot=SPOT, at=NOW)
    assert net_delta(mixed, underlier="TEST", contracts=contracts, surface=surface,
                     spot=SPOT, at=NOW) == pytest.approx(only_option - 40.0)


def test_the_hedge_is_what_flattens_the_book():
    surface, contracts = _hedge_setup()
    call = option(Right.CALL, 100.0)
    book = {call.symbol: 2.0}

    trade = hedge_quantity(book, underlier="TEST", contracts=contracts, surface=surface,
                           spot=SPOT, at=NOW)
    assert trade < 0.0, "long calls are hedged by selling the underlier"

    hedged = dict(book, TEST=trade)
    assert net_delta(hedged, underlier="TEST", contracts=contracts, surface=surface,
                     spot=SPOT, at=NOW) == pytest.approx(0.0, abs=1e-9)


def test_a_book_already_flat_enough_is_left_alone():
    """Chasing the last fraction of a share costs more in spread than the delta it removes."""
    surface, contracts = _hedge_setup()

    assert hedge_quantity({"TEST": 0.4}, underlier="TEST", contracts=contracts, surface=surface,
                          spot=SPOT, at=NOW) == 0.0
    assert hedge_quantity({"TEST": 40.0}, underlier="TEST", contracts=contracts, surface=surface,
                          spot=SPOT, at=NOW) == pytest.approx(-40.0)


def test_gamma_says_how_fast_the_hedge_goes_stale():
    """A hedge is good until spot moves; gamma is the rate at which it stops being one."""
    surface, contracts = _hedge_setup()
    call = option(Right.CALL, 100.0)
    book = {call.symbol: 2.0}

    gamma = net_gamma(book, underlier="TEST", contracts=contracts, surface=surface,
                      spot=SPOT, at=NOW)
    assert gamma > 0.0, "long options are long gamma"

    here = net_delta(book, underlier="TEST", contracts=contracts, surface=surface,
                     spot=SPOT, at=NOW)
    higher = net_delta(book, underlier="TEST", contracts=contracts, surface=surface,
                       spot=SPOT + 1.0, at=NOW)
    assert higher - here == pytest.approx(gamma, rel=0.05)

    # Shares carry delta but no gamma, so a hedge does not change how fast the book drifts.
    hedged = dict(book, TEST=-here)
    assert net_gamma(hedged, underlier="TEST", contracts=contracts, surface=surface,
                     spot=SPOT, at=NOW) == pytest.approx(gamma)
