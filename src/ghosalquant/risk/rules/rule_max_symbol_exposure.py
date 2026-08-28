import logging

from ...core import metadata_keys
from ...core.types import Liquidity, SignalType
from ..sizing import sizing_capital
from ..types import OrderDraft, RiskContext, RiskRule

logger = logging.getLogger(__name__)

EXPOSURE_EQUITY = "symbol_exposure_equity"
EXPOSURE_LIMIT_NOTIONAL = "symbol_exposure_limit_notional"
EXPOSURE_COMMITTED_NOTIONAL = "symbol_exposure_committed_notional"
EXPOSURE_UNIT_COST = "symbol_exposure_unit_cost"
EXPOSURE_CAPPED_FROM = "symbol_exposure_capped_from_quantity"


def remaining_exposure_quantity(
    *, notional_limit: float, committed_notional: float, unit_cost: float
) -> float:
    """How much more can be bought before a symbol reaches its notional ceiling."""
    if unit_cost <= 0.0:
        raise ValueError("unit_cost must be > 0")
    return max(0.0, (notional_limit - max(0.0, committed_notional)) / unit_cost)


class MaxSymbolExposure(RiskRule):
    """
    Cap what one symbol may hold, as a fraction of sizing capital.

    ``MaxOpenPositions`` counts positions; this measures them, which is the difference between
    three positions and three positions where one is the whole account. Resting buys count against
    the ceiling, because an unfilled bid is capital already committed, and a taker's fee is
    charged into the unit cost so the ceiling still holds once fees are paid.

    An order over the ceiling is trimmed rather than dropped; only one with no room left is
    blocked.
    """

    def __init__(
        self,
        *,
        capital: float,
        max_notional_fraction: float,
        taker_fee_bps: float = 0.0,
        reference_capital: float | None = None,
    ) -> None:
        if not 0.0 < max_notional_fraction <= 1.0:
            raise ValueError("max_notional_fraction must be in (0, 1]")
        if taker_fee_bps < 0.0:
            raise ValueError("taker_fee_bps must be >= 0")
        self.capital = float(capital)
        self.max_notional_fraction = float(max_notional_fraction)
        self.taker_fee_bps = float(taker_fee_bps)
        self.reference_capital = reference_capital
        sizing_capital(RiskContext(), capital=self.capital, reference_capital=reference_capital)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side is not SignalType.BUY or draft.quantity is None or draft.price <= 0.0:
            return True

        equity = sizing_capital(
            context, capital=self.capital, reference_capital=self.reference_capital
        )
        notional_limit = equity * self.max_notional_fraction
        committed = self._committed_notional(draft, context)
        unit_cost = self._unit_cost(draft, context)
        remaining = remaining_exposure_quantity(
            notional_limit=notional_limit, committed_notional=committed, unit_cost=unit_cost
        )
        draft.metadata.update({
            EXPOSURE_EQUITY: equity,
            EXPOSURE_LIMIT_NOTIONAL: notional_limit,
            EXPOSURE_COMMITTED_NOTIONAL: committed,
            EXPOSURE_UNIT_COST: unit_cost,
        })

        if remaining <= 0.0:
            logger.info(
                "MaxSymbolExposure blocked BUY %s: %.2f committed of %.2f (%.2f%% of %.2f)",
                draft.symbol, committed, notional_limit, self.max_notional_fraction * 100.0, equity,
            )
            return False

        requested = float(draft.quantity)
        if requested > remaining:
            draft.quantity = remaining
            draft.metadata[EXPOSURE_CAPPED_FROM] = requested
            logger.info(
                "MaxSymbolExposure capped BUY %s: %.4f -> %.4f (limit %.2f)",
                draft.symbol, requested, remaining, notional_limit,
            )
        return True

    def _committed_notional(self, draft: OrderDraft, context: RiskContext) -> float:
        multiplier = context.contracts.get(draft.symbol).multiplier
        quantity = float(context.open_qty_by_symbol.get(draft.symbol, 0.0))
        held = float(
            context.position_cost_by_symbol.get(
                draft.symbol, quantity * float(draft.price) * multiplier
            )
        )
        resting = sum(
            float(view.remaining_quantity) * float(view.limit_price or draft.price) * multiplier
            for view in context.open_orders
            if view.symbol == draft.symbol and view.order.side is SignalType.BUY
        )
        return max(0.0, held) + max(0.0, resting)

    def _unit_cost(self, draft: OrderDraft, context: RiskContext) -> float:
        liquidity = draft.liquidity or draft.metadata.get(metadata_keys.LIQUIDITY)
        taker = str(getattr(liquidity, "value", liquidity)).lower() == Liquidity.TAKER.value
        fee_rate = self.taker_fee_bps / 10_000.0 if taker else 0.0
        multiplier = context.contracts.get(draft.symbol).multiplier
        return float(draft.price) * multiplier * (1.0 + fee_rate)
