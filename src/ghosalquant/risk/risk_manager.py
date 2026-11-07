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
    def __init__(self, rules: Iterable[RiskRule] | None = None, *, cash_buffer: float = 0.01) -> None:
        if rules is None:
            rules = (
                MaxOpenPositions(),
                ConfidenceSizer(),
            )
        if not 0.0 <= cash_buffer < 1.0:
            raise ValueError("cash_buffer must be in [0, 1)")

        self.rules = list(rules)
        # Share of equity a target-weight buy leaves in cash, for the move before it fills and costs.
        self.cash_buffer = float(cash_buffer)
        self._warned: set[tuple[str, str]] = set()

    def _warn_once(self, symbol: str, reason: str, message: str, *args) -> None:
        # A strategy that signals every bar would otherwise repeat the warning on every bar.
        if (symbol, reason) in self._warned:
            logger.debug(message, *args)
            return
        self._warned.add((symbol, reason))
        logger.warning(message, *args)

    def _resolve_target_weight(self, draft: OrderDraft, context: RiskContext) -> bool:
        """Turn a ``TARGET_WEIGHT`` into the trade that brings the position to it."""
        raw_weight = draft.metadata.get(metadata_keys.TARGET_WEIGHT)
        if raw_weight is None:
            return True

        equity = current_equity(context)
        if equity is None or equity <= 0.0 or draft.price <= 0.0:
            self._warn_once(
                draft.symbol, "no_equity",
                "RiskManager dropped %s signal: a target weight needs positive equity and price",
                draft.symbol,
            )
            return False

        target_weight = float(raw_weight)
        if not math.isfinite(target_weight) or target_weight < 0.0:
            self._warn_once(
                draft.symbol, "bad_weight",
                "RiskManager dropped %s signal: invalid target weight %r", draft.symbol, raw_weight,
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

        # The portfolio refuses a fill it can't pay for, so size the buy to what cash covers.
        if delta_qty > _QTY_EPS and context.cash is not None:
            affordable = self._affordable_buy(draft.symbol, held_qty, unit_cost, equity, context)
            if affordable < delta_qty:
                if affordable <= _QTY_EPS and held_qty <= _QTY_EPS:
                    self._warn_once(
                        draft.symbol, "no_cash",
                        "RiskManager dropped %s buy: cash %.2f covers none of it after keeping "
                        "%.1f%% of equity in cash (RISK.cash_buffer)",
                        draft.symbol, context.cash, self.cash_buffer * 100.0,
                    )
                else:
                    logger.info(
                        "RiskManager trimmed %s buy from %.6g to %.6g, what cash covers",
                        draft.symbol, delta_qty, affordable,
                    )
                delta_qty = affordable

        if abs(delta_qty) <= _QTY_EPS:
            draft.quantity = 0.0
            return True

        draft.side = SignalType.BUY if delta_qty > 0.0 else SignalType.SELL
        draft.quantity = abs(delta_qty)
        return True

    def _affordable_buy(
            self, symbol: str, held_qty: float, unit_cost: float, equity: float, context: RiskContext,
    ) -> float:
        """The largest buy of ``symbol`` cash pays for, after resting buys and the buffer."""
        # Buying back a short only closes it, which the portfolio never refuses for cash.
        covering = max(0.0, -held_qty)
        resting_cost = sum(
            view.notional * context.contracts.get(view.symbol).multiplier
            for view in context.open_orders
            if view.side is SignalType.BUY
        )
        spare = context.cash - self.cash_buffer * equity - resting_cost - covering * unit_cost
        return covering + max(0.0, spare / unit_cost)

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

        if draft.quantity is None:
            self._warn_once(
                draft.symbol, "unsized",
                "RiskManager dropped %s signal: it has no quantity or TARGET_WEIGHT, and no "
                "risk rule sized it",
                draft.symbol,
            )
            return None

        # Zero is normal: a target weight the position already meets.
        if draft.quantity <= 0:
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
