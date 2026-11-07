from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Sequence

from ..core.contracts import ContractRegistry
from ..core.types import Liquidity, OpenOrderView, SignalType


@dataclass(frozen=True, slots=True)
class Window:
    """
    A rolling window, and how much history it needs before its answer means anything.

    The last ``size`` observations are used once ``ramp_up`` of them exist; below that there is no
    answer rather than a weak one. Slicing a series directly returns whatever is there, and a
    correlation over three points is noise a rule then acts on. ``ramp_up`` defaults to ``size``.
    """

    size: int
    ramp_up: int | None = None

    def __post_init__(self) -> None:
        if self.size <= 0:
            raise ValueError("window size must be > 0")
        if self.ramp_up is not None and not 0 < self.ramp_up <= self.size:
            raise ValueError("ramp_up must be > 0 and no larger than size")

    @property
    def required(self) -> int:
        """Observations needed before the window answers at all."""
        return self.size if self.ramp_up is None else self.ramp_up

    def of(self, series: Sequence[float] | None) -> list[float] | None:
        """The window's observations, or ``None`` while there are too few to trust."""
        if series is None or len(series) < self.required:
            return None
        return list(series[-self.size:])

@dataclass(slots=True)
class OrderDraft:
    """
    A draft order which is passed across risk rules for validation and sizing. Eventually converted to immutable Order.
    """
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float | None
    price: float
    metadata: dict[str, Any] = field(default_factory=dict)
    limit_price: float | None = None
    liquidity: Liquidity | None = None


@dataclass(slots=True)
class RiskContext:
    open_positions: int = 0 # Current open positions
    open_qty_by_symbol: dict[str, float] = field(default_factory=dict)
    position_cost_by_symbol: dict[str, float] = field(default_factory=dict)
    current_drawdown: float | None = None  # -0.05 means 5% below peak
    peak_equity: float | None = None
    current_equity: float | None = None
    cash: float | None = None
    price_history: dict[str, list[float]] | None = None # recent close prices per symbol
    open_orders: tuple[OpenOrderView, ...] = ()
    contracts: ContractRegistry = field(default_factory=ContractRegistry)

class RiskRule(Protocol):
    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        """ Returns False to drop the order draft. """
        ...
