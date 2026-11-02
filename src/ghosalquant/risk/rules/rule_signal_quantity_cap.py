from ...core import metadata_keys
from ..types import OrderDraft, RiskContext, RiskRule


class SignalQuantityCap(RiskRule):
    """
    Hold a sizer to the quantity the strategy asked for.

    A strategy watching the book knows how much size is actually displayed; a sizer working from
    capital alone does not, and will happily ask for more than the venue is showing. When the
    signal carries ``max_quantity`` this caps the draft at it, so sizing rules can run in any
    order without one of them undoing the strategy's own limit.
    """

    def __init__(self, *, metadata_key: str = metadata_keys.MAX_QUANTITY) -> None:
        self.metadata_key = metadata_key

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        cap = draft.metadata.get(self.metadata_key)
        if cap is None:
            return True
        cap_quantity = float(cap)
        if cap_quantity <= 0.0:
            return False
        draft.quantity = (
            cap_quantity if draft.quantity is None else min(float(draft.quantity), cap_quantity)
        )
        return draft.quantity > 0.0
