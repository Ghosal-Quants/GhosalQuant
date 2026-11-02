import math

from ..types import OrderDraft, RiskContext, RiskRule

_EPS = 1e-9


class IntegerQuantity(RiskRule):
    """
    Round a draft down to a whole number of tradable units.

    ``lot_size`` is the venue's minimum increment: 1 for shares, 100 for a board lot, 0.001 for a
    crypto pair. Rounding down rather than to nearest keeps a sizer from ever exceeding the budget
    it was given, and a draft that rounds away to nothing is dropped rather than sent as a zero.
    """

    def __init__(self, lot_size: float = 1.0) -> None:
        if lot_size <= 0.0:
            raise ValueError("lot_size must be > 0")
        self.lot_size = float(lot_size)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.quantity is None:
            return True
        lots = math.floor(float(draft.quantity) / self.lot_size + _EPS)
        draft.quantity = lots * self.lot_size
        return draft.quantity > 0.0
