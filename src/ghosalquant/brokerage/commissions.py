from dataclasses import dataclass
from typing import Protocol

from ..core.types import Liquidity


class CommissionModel(Protocol):
    """What a fill costs. Negative means the venue paid us."""

    def charge(self, *, price: float, quantity: float, liquidity: Liquidity | None) -> float | None:
        ...


@dataclass(frozen=True, slots=True)
class PerShareCommission:
    per_share: float = 0.0

    def charge(self, *, price: float, quantity: float, liquidity: Liquidity | None) -> float | None:
        if not self.per_share:
            return None
        return quantity * self.per_share


@dataclass(frozen=True, slots=True)
class MakerTakerCommission:
    """A rebate for adding liquidity, a fee for taking it, both in basis points of notional."""
    maker_rebate_bps: float
    taker_fee_bps: float

    def __post_init__(self) -> None:
        if self.maker_rebate_bps < 0.0 or self.taker_fee_bps < 0.0:
            raise ValueError("commission rates must be non-negative")

    def charge(self, *, price: float, quantity: float, liquidity: Liquidity | None) -> float:
        notional = price * quantity
        if liquidity is Liquidity.TAKER:
            return notional * (self.taker_fee_bps / 10_000.0)
        return -notional * (self.maker_rebate_bps / 10_000.0)
