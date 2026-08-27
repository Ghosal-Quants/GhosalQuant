import logging

from ..types import RiskRule, OrderDraft, RiskContext

logger = logging.getLogger(__name__)

def lerp(a: float, b: float, t: float) -> float:
    if t < 0.0:
        t = 0.0
    elif t > 1.0:
        t = 1.0

    return a + (b - a) * t

class ConfidenceSizer(RiskRule):
    def __init__(self, min_quantity: float = 0.0, max_quantity: float = 1.0) -> None:
        self.min_quantity = float(min_quantity)
        self.max_quantity = float(max_quantity)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        confidence: float = draft.metadata.get("confidence", 0.0)

        draft.quantity = float(lerp(self.min_quantity, self.max_quantity, confidence))

        if draft.quantity == 0.0:
            logger.info("ConfidenceSizer blocked %s: confidence too low", draft.symbol)
        return draft.quantity > 0.0
