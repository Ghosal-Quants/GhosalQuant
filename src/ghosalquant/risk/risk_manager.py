from typing import Iterable

from ..core import metadata_keys
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
        metadata = {} if signal.metadata is None else dict(signal.metadata)

        limit_price = signal.limit_price
        if limit_price is None and metadata.get(metadata_keys.LIMIT_PRICE) is not None:
            limit_price = float(metadata[metadata_keys.LIMIT_PRICE])

        draft = OrderDraft(
            time_stamp= signal.time_stamp,
            symbol= signal.symbol,
            side= signal.signal_type,
            quantity= signal.quantity,
            price= float(signal.ref_price),
            metadata= metadata,
            limit_price= limit_price,
            liquidity= signal.liquidity,
        )
        draft.metadata[metadata_keys.CONFIDENCE] = signal.confidence

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
            ref_price=draft.price,
            metadata=draft.metadata or None,
            limit_price=draft.limit_price,
            liquidity=draft.liquidity,
        )
