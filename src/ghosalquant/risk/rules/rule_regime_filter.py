import logging
from ..types import RiskRule, OrderDraft, RiskContext

logger = logging.getLogger(__name__)

class RegimeFilter(RiskRule):
    def __init__(
            self,
            blocked_regimes: list[str] | None = None,
            penalty_regimes: dict[str, float] | None = None,
    ) -> None:
        """
        Blocks orders generated during undesirable market regimes and optionally
        penalises sizing in transitional regimes (e.g. CHOPPY).

        Regime is read from ``draft.metadata["regime"]`` (case-insensitive).

        :param blocked_regimes: list[str]
            Regime names that cause the order to be dropped entirely.
            Default: ``["NOISE"]``.
        :param penalty_regimes: dict[str, float] | None
            Mapping of regime name -> quantity multiplier.  If the regime matches
            and ``draft.quantity`` is set, it is scaled by the multiplier.
            Default: ``{"CHOPPY": 0.6}``.
        """
        if blocked_regimes is None:
            blocked_regimes = ["NOISE"]
        if penalty_regimes is None:
            penalty_regimes = {"CHOPPY": 0.6}

        self.blocked_regimes = {r.upper() for r in blocked_regimes}
        self.penalty_regimes = {k.upper(): float(v) for k, v in penalty_regimes.items()}

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        regime: str = str(draft.metadata.get("regime", "")).strip().upper()

        if regime in self.blocked_regimes:
            logger.info("RegimeFilter blocked %s: regime %r", draft.symbol, regime)
            return False

        if regime in self.penalty_regimes and draft.quantity is not None:
            multiplier = self.penalty_regimes[regime]
            original = draft.quantity
            draft.quantity = draft.quantity * multiplier
            logger.info(
                "RegimeFilter penalised %s in regime %r: %.4f -> %.4f",
                draft.symbol, regime, original, draft.quantity,
            )

        return True
