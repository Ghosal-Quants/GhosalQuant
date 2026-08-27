import logging

from ...core.types import SignalType
from ..types import RiskRule, OrderDraft, RiskContext

logger = logging.getLogger(__name__)

class MaxOpenPositions(RiskRule):
    def __init__(self, max_open_positions: int = 3) -> None:
        self.max_open_positions = max_open_positions

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if (draft.side == SignalType.BUY) and (context.open_positions >= self.max_open_positions):
                logger.info(
                    "MaxOpenPositions blocked BUY %s: %d already open",
                    draft.symbol, self.max_open_positions,
                )
                return False
        else:
            return True
