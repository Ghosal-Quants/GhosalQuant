import logging
import math
from typing import Iterable

from ..core import metadata_keys
from ..core.types import Signal, SignalType, Order
from ..risk.sizing import current_equity
from ..risk.types import OrderDraft, RiskRule, RiskContext

# Rules
from ..risk.rules.rule_max_open_positions import MaxOpenPositions
from ..risk.rules.rule_confidence_sizer import ConfidenceSizer

logger = logging.getLogger(__name__)

_QTY_EPS = 1e-8


class RiskManager:
    def __init__(self, rules: Iterable[RiskRule] | None = None) -> None:
        if rules is None:
            rules = (
                MaxOpenPositions(),
                ConfidenceSizer(),
            )

        self.rules = list(rules)

    @staticmethod
    def _resolve_target_weight(draft: OrderDraft, context: RiskContext) -> bool:
        """Turn a ``TARGET_WEIGHT`` into the trade that brings the position to it."""
        raw_weight = draft.metadata.get(metadata_keys.TARGET_WEIGHT)
        if raw_weight is None:
            return True

        equity = current_equity(context)
        if equity is None or equity <= 0.0 or draft.price <= 0.0:
            logger.info(
                "RiskManager blocked %s: target weight needs positive equity and price",
                draft.symbol,
            )
            return False

        target_weight = float(raw_weight)
        if not math.isfinite(target_weight) or target_weight < 0.0:
            logger.info(
                "RiskManager blocked %s: invalid target weight %r", draft.symbol, raw_weight,
            )
            return False

        unit_cost = draft.price * context.contracts.get(draft.symbol).multiplier
        target_qty = equity * target_weight / unit_cost
        held_qty = float(context.open_qty_by_symbol.get(draft.symbol, 0.0))
        # Without this, every signal re-sends whatever has not filled yet.
        resting_qty = sum(
            view.remaining_quantity * (1.0 if view.order.side is SignalType.BUY else -1.0)
            for view in context.open_orders
            if view.symbol == draft.symbol
        )
        delta_qty = target_qty - held_qty - resting_qty
        draft.metadata[metadata_keys.TARGET_QUANTITY] = target_qty

        if abs(delta_qty) <= _QTY_EPS:
            draft.quantity = 0.0
            return True

        draft.side = SignalType.BUY if delta_qty > 0.0 else SignalType.SELL
        draft.quantity = abs(delta_qty)
        return True

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

        if not self._resolve_target_weight(draft, context):
            return None

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
