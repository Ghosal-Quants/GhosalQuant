from datetime import datetime
from typing import Mapping

from ..core.contracts import ContractRegistry
from ..core.volatility import VolSurface

# Trading a fraction of a share to chase a rounding error costs more in spread than it removes.
DEFAULT_TOLERANCE = 1.0


def net_delta(
    positions: Mapping[str, float],
    *,
    underlier: str,
    contracts: ContractRegistry,
    surface: VolSurface,
    spot: float,
    at: datetime,
    rate: float = 0.0,
    dividend: float = 0.0,
) -> float:
    """A book's exposure to one underlier, in shares of it. Other underliers are ignored."""
    total = 0.0
    for symbol, quantity in positions.items():
        if not quantity:
            continue
        contract = contracts.get(symbol)
        if contract.underlier != underlier:
            continue
        if contract.right is None:
            total += float(quantity)
            continue
        greeks = surface.greeks(contract, spot, at, rate=rate, dividend=dividend)
        if greeks is None:
            continue
        total += greeks.delta * float(quantity) * contract.multiplier
    return total


def hedge_quantity(
    positions: Mapping[str, float],
    *,
    underlier: str,
    contracts: ContractRegistry,
    surface: VolSurface,
    spot: float,
    at: datetime,
    rate: float = 0.0,
    dividend: float = 0.0,
    tolerance: float = DEFAULT_TOLERANCE,
) -> float:
    """
    What to trade in the underlier to flatten that exposure: positive buys, negative sells.

    Hedged, what is left is gamma against theta rather than a view on direction.
    """
    exposure = net_delta(
        positions, underlier=underlier, contracts=contracts, surface=surface,
        spot=spot, at=at, rate=rate, dividend=dividend,
    )
    return 0.0 if abs(exposure) < tolerance else -exposure
