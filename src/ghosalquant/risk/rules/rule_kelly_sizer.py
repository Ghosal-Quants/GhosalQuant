import logging

from ...core import metadata_keys
from ...core.types import SignalType
from ..sizing import kelly_fraction, sizing_capital
from ..types import OrderDraft, RiskContext, RiskRule

logger = logging.getLogger(__name__)

KELLY_FULL = "kelly_full_fraction"
KELLY_STAKED = "kelly_staked_fraction"


class FractionalKellySizer(RiskRule):
    """
    Size a buy by its edge, from the signal's ``confidence``, ``take_profit`` and ``stop_loss``.

    Overbetting Kelly costs far more than underbetting it, and these inputs are estimated,
    hence ``fraction``. No edge is blocked rather than sized small.
    """

    def __init__(
        self,
        *,
        capital: float,
        fraction: float = 0.25,
        max_notional_fraction: float = 0.1,
        reference_capital: float | None = None,
    ) -> None:
        if not 0.0 < fraction <= 1.0:
            raise ValueError("fraction must be in (0, 1]")
        if not 0.0 < max_notional_fraction <= 1.0:
            raise ValueError("max_notional_fraction must be in (0, 1]")
        self.capital = float(capital)
        self.fraction = float(fraction)
        self.max_notional_fraction = float(max_notional_fraction)
        self.reference_capital = reference_capital
        sizing_capital(RiskContext(), capital=self.capital, reference_capital=reference_capital)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side is not SignalType.BUY or draft.price <= 0.0:
            return True

        edge = self._edge(draft)
        if edge is None:
            return True
        probability, gain, loss = edge

        full = kelly_fraction(probability, gain, loss)
        staked = min(full * self.fraction, self.max_notional_fraction)
        draft.metadata.update({KELLY_FULL: full, KELLY_STAKED: staked})
        if staked <= 0.0:
            logger.info(
                "FractionalKellySizer blocked BUY %s: no edge at p=%.2f, +%.0f%%/-%.0f%%",
                draft.symbol, probability, gain * 100.0, loss * 100.0,
            )
            return False

        equity = sizing_capital(
            context, capital=self.capital, reference_capital=self.reference_capital
        )
        unit_cost = float(draft.price) * context.contracts.get(draft.symbol).multiplier
        draft.quantity = staked * equity / unit_cost
        return draft.quantity > 0.0

    @staticmethod
    def _edge(draft: OrderDraft) -> tuple[float, float, float] | None:
        """The signal's own estimate, or ``None`` when it did not offer one."""
        probability = draft.metadata.get(metadata_keys.CONFIDENCE)
        gain = draft.metadata.get(metadata_keys.TAKE_PROFIT)
        loss = draft.metadata.get(metadata_keys.STOP_LOSS)
        if probability is None or gain is None or loss is None:
            return None
        return float(probability), abs(float(gain)), abs(float(loss))
