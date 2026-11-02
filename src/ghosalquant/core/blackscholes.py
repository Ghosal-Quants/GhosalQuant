import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .contracts import Contract, Right, intrinsic_value

# US equity options stop trading at 16:00 New York, 21:00 UTC outside daylight saving.
EXPIRY_TIME = time(21, 0)
YEAR = 365.0 * 24 * 3600
_MAX_VOL = 5.0
_MIN_VOL = 1e-4


@dataclass(frozen=True, slots=True)
class Greeks:
    """
    Sensitivities per contract, before the multiplier.

    Vega is per vol point and theta per day, as a chain quotes them.
    """
    delta: float
    gamma: float
    vega: float
    theta: float
    rho: float


def tenor(contract: Contract, at: datetime) -> float:
    """Years to expiry, counting the expiry date's own session as still live."""
    if contract.expiry is None:
        return 0.0
    return max((expires_at(contract.expiry, at) - at).total_seconds() / YEAR, 0.0)


def expires_at(expiry: date, like: datetime) -> datetime:
    """The moment an expiry date stops trading, in the timezone the events are stamped in."""
    return datetime.combine(expiry, EXPIRY_TIME).replace(tzinfo=like.tzinfo)


def price(
    contract: Contract,
    spot: float,
    vol: float,
    at: datetime,
    *,
    rate: float = 0.0,
    dividend: float = 0.0,
) -> float:
    """
    Black-Scholes, on a contract the registry already describes.

    Quoted vols are struck against the prevailing rate, so pricing without one misprices every
    contract in the same direction.
    """
    years = tenor(contract, at)
    if contract.right is None or contract.strike is None:
        return spot
    if years <= 0.0 or vol <= 0.0:
        return intrinsic_value(contract, spot)

    d1, d2 = _d(spot, contract.strike, years, vol, rate, dividend)
    carried_spot = spot * math.exp(-dividend * years)
    discounted_strike = contract.strike * math.exp(-rate * years)
    call = carried_spot * _cdf(d1) - discounted_strike * _cdf(d2)
    if contract.right is Right.CALL:
        return call
    return call - carried_spot + discounted_strike


def greeks(
    contract: Contract,
    spot: float,
    vol: float,
    at: datetime,
    *,
    rate: float = 0.0,
    dividend: float = 0.0,
) -> Greeks:
    """The five sensitivities, in the units a chain quotes them in."""
    years = tenor(contract, at)
    if contract.right is None or contract.strike is None or years <= 0.0 or vol <= 0.0:
        return Greeks(_expiry_delta(contract, spot), 0.0, 0.0, 0.0, 0.0)

    call_side = contract.right is Right.CALL
    root = math.sqrt(years)
    d1, d2 = _d(spot, contract.strike, years, vol, rate, dividend)
    carry = math.exp(-dividend * years)
    discount = math.exp(-rate * years)
    density = _pdf(d1)

    delta = carry * (_cdf(d1) if call_side else _cdf(d1) - 1.0)
    gamma = carry * density / (spot * vol * root)
    vega = spot * carry * density * root
    decay = -spot * carry * density * vol / (2.0 * root)
    if call_side:
        theta = decay - rate * contract.strike * discount * _cdf(d2) + dividend * spot * carry * _cdf(d1)
        rho = contract.strike * years * discount * _cdf(d2)
    else:
        theta = decay + rate * contract.strike * discount * _cdf(-d2) - dividend * spot * carry * _cdf(-d1)
        rho = -contract.strike * years * discount * _cdf(-d2)
    # Quoted per vol point and per day rather than per unit and per year.
    return Greeks(delta, gamma, vega / 100.0, theta / 365.0, rho / 100.0)


def implied_volatility(
    observed: float,
    contract: Contract,
    spot: float,
    at: datetime,
    *,
    rate: float = 0.0,
    dividend: float = 0.0,
    tolerance: float = 1e-6,
) -> float | None:
    """
    The vol that reprices ``observed``, or ``None`` when no vol does.

    Bisection rather than Newton: vega collapses on the wings, where a Newton step walks off.
    """
    years = tenor(contract, at)
    if contract.right is None or years <= 0.0 or observed <= 0.0:
        return None
    # Discounted intrinsic, not spot: an in-the-money put sits below strike minus spot.
    if observed < _lower_bound(contract, spot, years, rate, dividend) - tolerance:
        return None

    low, high = _MIN_VOL, _MAX_VOL
    if price(contract, spot, high, at, rate=rate, dividend=dividend) < observed:
        return None
    for _ in range(100):
        mid = 0.5 * (low + high)
        if price(contract, spot, mid, at, rate=rate, dividend=dividend) < observed:
            low = mid
        else:
            high = mid
        if high - low < tolerance:
            break
    vol = 0.5 * (low + high)
    return vol if vol > _MIN_VOL * 2.0 else None


def _lower_bound(contract: Contract, spot: float, years: float, rate: float, dividend: float) -> float:
    forward = spot * math.exp(-dividend * years)
    discounted_strike = contract.strike * math.exp(-rate * years)
    if contract.right is Right.CALL:
        return max(forward - discounted_strike, 0.0)
    return max(discounted_strike - forward, 0.0)


def _d(spot: float, strike: float, years: float, vol: float, rate: float, dividend: float):
    drift = (rate - dividend + 0.5 * vol * vol) * years
    d1 = (math.log(spot / strike) + drift) / (vol * math.sqrt(years))
    return d1, d1 - vol * math.sqrt(years)


def _expiry_delta(contract: Contract, spot: float) -> float:
    if contract.right is None or contract.strike is None:
        return 1.0
    if contract.right is Right.CALL:
        return 1.0 if spot > contract.strike else 0.0
    return -1.0 if spot < contract.strike else 0.0


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
