from typing import Iterable

from ..core.types import Signal, SignalType, Order
from ..risk.types import OrderDraft, RiskRule, RiskContext

# Rules
from ..risk.rules.rule_max_open_positions import MaxOpenPositions
from ..risk.rules.rule_confidence_sizer import ConfidenceSizer


class RiskManager:
    def __init__(self, rules: Iterable[RiskRule] | None = None) -> None:
        if rules is None:
            rules = (
                MaxOpenPositions(),
                ConfidenceSizer(),
            )

        self.rules = list(rules)

    def evaluate(self, signal: Signal, context: RiskContext) -> Order | None:
        draft = OrderDraft(
            time_stamp= signal.time_stamp,
            symbol= signal.symbol,
            side= signal.signal_type,
            quantity= signal.quantity,
            price= float(signal.price),
            metadata= {} if signal.metadata is None else dict(signal.metadata)
        )
        draft.metadata["confidence"] = signal.confidence

        for rule in self.rules:
            if not rule.apply(draft, context):
                return None

        if draft.quantity is None or draft.quantity <= 0:
            return None

        return Order(
            time_stamp=draft.time_stamp,
            symbol=draft.symbol,
            side=draft.side,
            quantity=draft.quantity,
            price=draft.price,
            metadata=draft.metadata or None
        )
