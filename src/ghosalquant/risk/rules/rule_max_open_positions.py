from ...core.types import SignalType
from ..types import RiskRule, OrderDraft, RiskContext

class MaxOpenPositions(RiskRule):
    def __init__(self, max_open_positions: int = 3) -> None:
        self.max_open_positions = max_open_positions

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side == SignalType.BUY:
            if RiskContext.open_positions >= self.max_open_positions:
                print(f"[MaxOpenPositions] Blocked BUY for {draft.symbol}: max open positions reached ({self.max_open_positions})")
                return False
        else:
            return True
