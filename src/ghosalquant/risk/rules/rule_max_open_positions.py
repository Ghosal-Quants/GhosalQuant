import logging

from ...core.types import SignalType
from ..types import RiskRule, OrderDraft, RiskContext

logger = logging.getLogger(__name__)

class MaxOpenPositions(RiskRule):
    def __init__(self, max_open_positions: int = 3) -> None:
        self.max_open_positions = max_open_positions

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side != SignalType.BUY:
            return True

        # A buy in a symbol already held, long or short, opens nothing new.
        if context.open_qty_by_symbol.get(draft.symbol, 0.0) != 0.0:
            return True

        if context.open_positions >= self.max_open_positions:
            logger.info(
                "MaxOpenPositions blocked BUY %s: %d already open",
                draft.symbol, self.max_open_positions,
            )
            return False

        return True
