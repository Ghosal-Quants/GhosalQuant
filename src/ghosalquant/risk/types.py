from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from ..core.types import SignalType, Order

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


@dataclass(slots=True)
class RiskContext:
    open_positions: int = 0 # Current open positions
    open_qty_by_symbol: dict[str, float] = field(default_factory=dict)


class RiskRule(Protocol):
    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        """ Returns False to drop the order draft. """
        ...
