from ...core import metadata_keys
from ...core.types import SignalType
from ..sizing import sizing_capital
from ..types import OrderDraft, RiskContext, RiskRule


class SymbolCapitalFractionSizer(RiskRule):
    """
    Size a buy by the money it should spend, not the number of units it should buy.

    ``ConfidenceSizer`` maps confidence to a quantity, so the same setting buys $50 of a $5 stock
    and $5,000 of a $500 one. This gives each symbol a share of capital instead, tops up towards
    that share rather than adding to it, and sells whatever is held. A buy whose metadata sets
    ``presized_key`` true keeps the quantity it came with, for a sleeve sized from its own budget.
    """

    def __init__(
        self,
        *,
        capital: float,
        max_notional_fraction_by_symbol: dict[str, float] | None = None,
        default_max_notional_fraction: float | None = None,
        min_quantity: float = 0.0,
        scale_by_confidence: bool = True,
        reference_capital: float | None = None,
        presized_key: str | None = None,
    ) -> None:
        fractions = dict(max_notional_fraction_by_symbol or {})
        if not fractions and default_max_notional_fraction is None:
            raise ValueError("a per-symbol fraction or a default fraction is required")
        for symbol, fraction in fractions.items():
            if fraction <= 0.0:
                raise ValueError(f"fraction for {symbol} must be > 0")
        if default_max_notional_fraction is not None and default_max_notional_fraction <= 0.0:
            raise ValueError("default_max_notional_fraction must be > 0")
        self.capital = float(capital)
        self.max_notional_fraction_by_symbol = {
            str(symbol): float(fraction) for symbol, fraction in fractions.items()
        }
        self.default_max_notional_fraction = default_max_notional_fraction
        self.min_quantity = float(min_quantity)
        self.scale_by_confidence = bool(scale_by_confidence)
        self.reference_capital = reference_capital
        self.presized_key = presized_key
        sizing_capital(RiskContext(), capital=self.capital, reference_capital=reference_capital)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.price <= 0.0:
            return False

        held = float(context.open_qty_by_symbol.get(draft.symbol, 0.0))
        if draft.side is SignalType.SELL:
            requested = held if draft.quantity is None else min(float(draft.quantity), held)
            draft.quantity = max(0.0, requested)
            return draft.quantity > 0.0

        if self.presized_key is not None and draft.metadata.get(self.presized_key) is True and draft.quantity is not None:
            return float(draft.quantity) > 0.0

        fraction = self.max_notional_fraction_by_symbol.get(
            draft.symbol, self.default_max_notional_fraction
        )
        if fraction is None:
            return False

        confidence = 1.0
        if self.scale_by_confidence:
            confidence = min(1.0, max(0.0, float(draft.metadata.get(metadata_keys.CONFIDENCE, 1.0))))
        equity = sizing_capital(
            context, capital=self.capital, reference_capital=self.reference_capital
        )
        target_notional = equity * fraction * confidence
        unit_cost = float(draft.price) * context.contracts.get(draft.symbol).multiplier
        held_notional = float(
            context.position_cost_by_symbol.get(draft.symbol, held * unit_cost)
        )
        draft.quantity = max(0.0, target_notional - held_notional) / unit_cost
        return draft.quantity > 0.0 and draft.quantity >= self.min_quantity
