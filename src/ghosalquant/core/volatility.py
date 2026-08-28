import math
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping

from . import blackscholes
from .blackscholes import Greeks
from .contracts import Contract, ContractRegistry
from .types import OrderBook


@dataclass(frozen=True, slots=True)
class Smile:
    """One expiry's implied vols, by log-moneyness, in increasing strike order."""

    tenor: float
    moneyness: tuple[float, ...]
    vols: tuple[float, ...]

    def vol(self, moneyness: float) -> float:
        return _interpolate(self.moneyness, self.vols, moneyness)


@dataclass(frozen=True, slots=True)
class VolSurface:
    """
    Implied volatility by log-moneyness and tenor, read off a chain rather than assumed.

    Strikes interpolate within an expiry; expiries interpolate in total variance, which stops two
    expiries implying a negative variance for the stretch between them. Outside the quoted range
    the nearest value is held rather than extrapolated.
    """

    smiles: tuple[Smile, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.smiles)

    @classmethod
    def from_quotes(
        cls,
        quotes: Mapping[str, float],
        *,
        contracts: ContractRegistry,
        spot: float,
        at: datetime,
        rate: float = 0.0,
        dividend: float = 0.0,
    ) -> "VolSurface":
        """Invert a chain's prices into vols, skipping anything that does not invert."""
        by_tenor: dict[float, list[tuple[float, float]]] = {}
        for symbol, observed in quotes.items():
            contract = contracts.get(symbol)
            if contract.right is None or contract.strike is None or spot <= 0.0:
                continue
            years = blackscholes.tenor(contract, at)
            if years <= 0.0:
                continue
            vol = blackscholes.implied_volatility(
                observed, contract, spot, at, rate=rate, dividend=dividend
            )
            if vol is None:
                continue
            by_tenor.setdefault(years, []).append((math.log(contract.strike / spot), vol))

        smiles = []
        for years in sorted(by_tenor):
            points = _averaged(by_tenor[years])
            if points:
                smiles.append(Smile(years, tuple(m for m, _ in points), tuple(v for _, v in points)))
        return cls(tuple(smiles))

    @classmethod
    def from_books(
        cls,
        books: Iterable[OrderBook],
        *,
        contracts: ContractRegistry,
        spot: float,
        at: datetime,
        rate: float = 0.0,
        dividend: float = 0.0,
    ) -> "VolSurface":
        """The same, from the quotes the system already receives."""
        quotes = {}
        for book in books:
            if book.best_bid is not None and book.best_ask is not None:
                quotes[book.symbol] = (book.best_bid.price + book.best_ask.price) / 2.0
        return cls.from_quotes(
            quotes, contracts=contracts, spot=spot, at=at, rate=rate, dividend=dividend
        )

    def vol(self, contract: Contract, spot: float, at: datetime) -> float | None:
        """The surface's volatility for one contract, or ``None`` if it is empty."""
        if not self.smiles or contract.strike is None or spot <= 0.0:
            return None
        years = blackscholes.tenor(contract, at)
        if years <= 0.0:
            return None
        return self._at(math.log(contract.strike / spot), years)

    def price(
        self,
        contract: Contract,
        spot: float,
        at: datetime,
        *,
        rate: float = 0.0,
        dividend: float = 0.0,
    ) -> float | None:
        vol = self.vol(contract, spot, at)
        if vol is None:
            return None
        return blackscholes.price(contract, spot, vol, at, rate=rate, dividend=dividend)

    def greeks(
        self,
        contract: Contract,
        spot: float,
        at: datetime,
        *,
        rate: float = 0.0,
        dividend: float = 0.0,
    ) -> Greeks | None:
        vol = self.vol(contract, spot, at)
        if vol is None:
            return None
        return blackscholes.greeks(contract, spot, vol, at, rate=rate, dividend=dividend)

    def _at(self, moneyness: float, years: float) -> float:
        tenors = [smile.tenor for smile in self.smiles]
        index = bisect_left(tenors, years)
        if index == 0:
            return self.smiles[0].vol(moneyness)
        if index >= len(self.smiles):
            return self.smiles[-1].vol(moneyness)

        near, far = self.smiles[index - 1], self.smiles[index]
        # Interpolate total variance, not vol: variance is what adds along the time axis.
        near_variance = near.vol(moneyness) ** 2 * near.tenor
        far_variance = far.vol(moneyness) ** 2 * far.tenor
        weight = (years - near.tenor) / (far.tenor - near.tenor)
        variance = near_variance + weight * (far_variance - near_variance)
        return math.sqrt(max(variance, 0.0) / years)


def _averaged(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """One vol per strike: a call and a put on the same strike are two reads of one number."""
    grouped: dict[float, list[float]] = {}
    for moneyness, vol in points:
        grouped.setdefault(moneyness, []).append(vol)
    return [(m, sum(vs) / len(vs)) for m, vs in sorted(grouped.items())]


def _interpolate(xs: tuple[float, ...], ys: tuple[float, ...], x: float) -> float:
    if len(xs) == 1 or x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_left(xs, x)
    x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
