import logging
from ...core.types import SignalType
from ..types import RiskRule, OrderDraft, RiskContext

logger = logging.getLogger(__name__)

class DrawdownKillSwitch(RiskRule):
    def __init__(
            self,
            reduce_threshold: float = -0.08,
            kill_threshold: float = -0.15,
            reduce_multiplier: float = 0.5,
    ) -> None:
        """
        Two-tier drawdown protection for new BUY orders.

        1. **Kill switch** – if ``current_drawdown <= kill_threshold`` all new
           BUY orders are blocked.
        2. **Risk reduction** – if ``current_drawdown <= reduce_threshold`` the
           order quantity is scaled down by ``reduce_multiplier``.

        Drawdown is expected as a *negative* fraction in ``context.current_drawdown``
        (e.g. ``-0.10`` means 10 % below the peak).

        If ``current_drawdown`` is ``None`` (not yet available) the rule passes
        through without modification.

        :param reduce_threshold: float
            Drawdown level at which position sizes start being reduced.
            Default ``-0.08`` (8 %).
        :param kill_threshold: float
            Drawdown level at which all new BUY orders are blocked.
            Default ``-0.15`` (15 %).
        :param reduce_multiplier: float
            Factor to scale quantity by when in the reduction zone.
            Default ``0.5``.
        """
        self.reduce_threshold = float(reduce_threshold)
        self.kill_threshold = float(kill_threshold)
        self.reduce_multiplier = float(reduce_multiplier)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        # Only gate BUY orders
        if draft.side != SignalType.BUY:
            return True

        dd = context.current_drawdown
        if dd is None:
            return True  # No drawdown info yet, pass through

        # Kill switch
        if dd <= self.kill_threshold:
            logger.warning(
                "DrawdownKillSwitch blocked %s: drawdown %.2f%% past kill threshold %.2f%%",
                draft.symbol, dd * 100.0, self.kill_threshold * 100.0,
            )
            return False

        # Reduce exposure
        if dd <= self.reduce_threshold and draft.quantity is not None:
            original = draft.quantity
            draft.quantity = draft.quantity * self.reduce_multiplier
            logger.info(
                "DrawdownKillSwitch reduced %s at %.2f%% drawdown: %.4f -> %.4f",
                draft.symbol, dd * 100.0, original, draft.quantity,
            )

        return True
