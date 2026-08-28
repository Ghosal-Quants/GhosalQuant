from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Iterable

from .types import Fill, SignalType

OCC_LENGTH = 21
# The OCC exercises anything in the money by this much, and abandons the rest.
EXERCISE_THRESHOLD = 0.01


class Right(str, Enum):
    """Which side of the underlier an option gives. ``None`` on a contract means it is not one."""
    CALL = "call"
    PUT = "put"


class Settlement(str, Enum):
    CASH = "cash"
    PHYSICAL = "physical"


@dataclass(frozen=True, slots=True)
class Contract:
    """
    What a symbol is, for the parts of the system that need to know.

    The symbol stays whatever the venue calls it, so events, brokers and recordings are
    unaffected. ``underlier`` is a share's own symbol, so netting against the stock is one lookup.
    """
    symbol: str
    underlier: str
    multiplier: float = 1.0
    right: Right | None = None
    strike: float | None = None
    expiry: date | None = None
    settlement: Settlement = Settlement.PHYSICAL

    def __post_init__(self) -> None:
        if self.multiplier <= 0.0:
            raise ValueError("Contract multiplier must be > 0")
        if self.strike is not None and self.strike <= 0.0:
            raise ValueError("Contract strike must be > 0")
        if self.right is not None and (self.strike is None or self.expiry is None):
            raise ValueError("An option needs a strike and an expiry")

    @classmethod
    def equity(cls, symbol: str) -> "Contract":
        return cls(symbol=symbol, underlier=symbol)

    @classmethod
    def from_occ(
        cls,
        symbol: str,
        *,
        multiplier: float = 100.0,
        settlement: Settlement = Settlement.PHYSICAL,
    ) -> "Contract":
        """
        Read an OCC symbol, the 21-character form the US option venues use.

        Called deliberately, never automatically: a symbol means what it was registered as, not
        what its shape suggests.
        """
        if len(symbol) != OCC_LENGTH:
            raise ValueError(f"An OCC symbol is {OCC_LENGTH} characters: {symbol!r}")
        right = symbol[12].upper()
        if right not in ("C", "P"):
            raise ValueError(f"An OCC symbol carries C or P, not {symbol[12]!r}")
        return cls(
            symbol=symbol,
            underlier=symbol[:6].strip(),
            multiplier=multiplier,
            right=Right.CALL if right == "C" else Right.PUT,
            strike=int(symbol[13:]) / 1000.0,
            expiry=datetime.strptime(symbol[6:12], "%y%m%d").date(),
            settlement=settlement,
        )


class ContractRegistry:
    """
    What each traded symbol means.

    A symbol nobody registered is a share of itself, so a registry only has to describe what is
    not one, and an empty registry behaves exactly as no registry at all.
    """

    def __init__(self, contracts: Iterable[Contract] = ()) -> None:
        self._by_symbol = {contract.symbol: contract for contract in contracts}

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._by_symbol

    def get(self, symbol: str) -> Contract:
        contract = self._by_symbol.get(symbol)
        return Contract.equity(symbol) if contract is None else contract

    def register(self, contract: Contract) -> None:
        self._by_symbol[contract.symbol] = contract

    @property
    def contracts(self) -> tuple[Contract, ...]:
        return tuple(self._by_symbol.values())


def intrinsic_value(contract: Contract, underlier_price: float) -> float:
    """What an option is worth at expiry, and nothing for anything that is not one."""
    if contract.right is None or contract.strike is None:
        return 0.0
    if contract.right is Right.CALL:
        return max(underlier_price - contract.strike, 0.0)
    return max(contract.strike - underlier_price, 0.0)


def settlement_fills(
    contract: Contract, quantity: float, underlier_price: float, *, at: datetime
) -> list[Fill]:
    """
    The fills that settle a position at expiry.

    Cash settlement closes the option at intrinsic. Physical settlement converts it: the option
    closes at nothing and the underlier trades at the strike. Anything not worth
    ``EXERCISE_THRESHOLD`` is abandoned.
    """
    if contract.right is None or contract.expiry is None or abs(quantity) <= 0.0:
        return []

    held_long = quantity > 0.0
    intrinsic = intrinsic_value(contract, underlier_price)
    cash_settled = contract.settlement is Settlement.CASH
    exercised = not cash_settled and intrinsic >= EXERCISE_THRESHOLD

    fills = [
        Fill(
            time_stamp=at,
            symbol=contract.symbol,
            side=SignalType.SELL if held_long else SignalType.BUY,
            quantity=abs(quantity),
            price=intrinsic if cash_settled else 0.0,
        )
    ]
    if exercised:
        buys_underlier = (contract.right is Right.CALL) == held_long
        fills.append(
            Fill(
                time_stamp=at,
                symbol=contract.underlier,
                side=SignalType.BUY if buys_underlier else SignalType.SELL,
                quantity=abs(quantity) * contract.multiplier,
                price=contract.strike,
            )
        )
    return fills
