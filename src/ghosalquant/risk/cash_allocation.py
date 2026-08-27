from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping
import logging

from ..core import metadata_keys
from ..core.types import OpenOrderView, SignalType
from .sizing import sizing_capital
from .types import OrderDraft, RiskContext, RiskRule

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "default"

ALLOCATION_BUCKET = "cash_allocation_bucket"
ALLOCATION_CAPITAL = "cash_allocation_capital"
ALLOCATION_HEADROOM = "cash_allocation_headroom_notional"
ALLOCATION_CAPPED_FROM = "cash_allocation_capped_from_quantity"


@dataclass(frozen=True, slots=True)
class CashAllocationConfig:
    """
    How much of the account may sit in resting buy orders at once.

    A strategy resting a wide ladder can reserve nearly all its cash on orders that rarely fill,
    and then have nothing left when a good opportunity finally shows. The caps are fractions of
    sizing capital rather than cash amounts, so the same policy holds in a backtest and live.

    Orders are grouped into buckets by the rule's classifier, and each bucket gets its own share
    of capital and its own per-order ceiling. One unnamed bucket is a perfectly good setup; the
    buckets are there for when a ladder and an opportunistic taker should not compete.
    """

    total_fraction: float = 0.9
    max_symbol_fraction: float = 0.3
    bucket_fractions: Mapping[str, float] = field(default_factory=dict)
    max_order_fractions: Mapping[str, float] = field(default_factory=dict)
    default_bucket_fraction: float = 1.0
    default_max_order_fraction: float = 1.0

    def __post_init__(self) -> None:
        for name, value in (
            ("total_fraction", self.total_fraction),
            ("max_symbol_fraction", self.max_symbol_fraction),
            ("default_bucket_fraction", self.default_bucket_fraction),
            ("default_max_order_fraction", self.default_max_order_fraction),
        ):
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be in (0, 1]")
        for label, fractions in (
            ("bucket_fractions", self.bucket_fractions),
            ("max_order_fractions", self.max_order_fractions),
        ):
            for bucket, value in fractions.items():
                if not 0.0 < value <= 1.0:
                    raise ValueError(f"{label}[{bucket}] must be in (0, 1]")

    def bucket_fraction(self, bucket: str) -> float:
        return float(self.bucket_fractions.get(bucket, self.default_bucket_fraction))

    def max_order_fraction(self, bucket: str) -> float:
        return float(self.max_order_fractions.get(bucket, self.default_max_order_fraction))


@dataclass(frozen=True, slots=True)
class CashReclamationPlan:
    """Which resting orders to cancel, and what cancelling them frees."""

    order_slots: tuple[str, ...] = ()
    reclaimed_notional: float = 0.0


def bucket_of(draft: OrderDraft) -> str:
    """The default classifier: whatever the signal named, else one shared bucket."""
    bucket = draft.metadata.get(metadata_keys.ALLOCATION_BUCKET)
    return DEFAULT_BUCKET if bucket is None else str(bucket)


def resting_buy_notional(
    open_orders: Iterable[OpenOrderView],
    *,
    symbol: str | None = None,
    bucket: str | None = None,
) -> float:
    """What the resting buys already reserve, optionally for one symbol or one bucket."""
    total = 0.0
    for view in open_orders:
        if view.order.side is not SignalType.BUY:
            continue
        if symbol is not None and view.symbol != symbol:
            continue
        if bucket is not None and _view_bucket(view) != bucket:
            continue
        price = view.limit_price or view.order.ref_price
        total += max(0.0, float(view.remaining_quantity)) * max(0.0, float(price or 0.0))
    return total


class CashAllocationRule(RiskRule):
    """
    Keep dry powder: trim a buy to what the account can still commit to resting orders.

    Four ceilings apply at once — the account total, this symbol, this bucket, and this one order
    — and the draft is trimmed to the tightest of them. Anything with no headroom left is blocked
    rather than sent, since the cash behind it is already spoken for.
    """

    def __init__(
        self,
        config: CashAllocationConfig,
        *,
        capital: float,
        classify: Callable[[OrderDraft], str] = bucket_of,
        reference_capital: float | None = None,
    ) -> None:
        self.config = config
        self.capital = float(capital)
        self.classify = classify
        self.reference_capital = reference_capital
        sizing_capital(RiskContext(), capital=self.capital, reference_capital=reference_capital)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side is not SignalType.BUY or draft.quantity is None or draft.price <= 0.0:
            return True

        capital = sizing_capital(
            context, capital=self.capital, reference_capital=self.reference_capital
        )
        bucket = self.classify(draft)
        headroom = min(
            capital * self.config.total_fraction - resting_buy_notional(context.open_orders),
            capital * self.config.max_symbol_fraction
            - resting_buy_notional(context.open_orders, symbol=draft.symbol),
            capital * self.config.bucket_fraction(bucket)
            - resting_buy_notional(context.open_orders, bucket=bucket),
            capital * self.config.max_order_fraction(bucket),
        )
        draft.metadata.update({
            ALLOCATION_BUCKET: bucket,
            ALLOCATION_CAPITAL: capital,
            ALLOCATION_HEADROOM: headroom,
        })

        if headroom <= 0.0:
            logger.info("CashAllocation blocked BUY %s: no headroom in %s", draft.symbol, bucket)
            return False

        allowed = headroom / float(draft.price)
        requested = float(draft.quantity)
        if requested > allowed:
            draft.quantity = allowed
            draft.metadata[ALLOCATION_CAPPED_FROM] = requested
            logger.info(
                "CashAllocation capped BUY %s: %.4f -> %.4f (headroom %.2f in %s)",
                draft.symbol, requested, allowed, headroom, bucket,
            )
        return draft.quantity > 0.0


def plan_cash_reclamation(
    *,
    required_notional: float,
    open_orders: Iterable[OpenOrderView],
    symbol: str,
    candidate_strength: float,
    strength: Callable[[OpenOrderView], float],
) -> CashReclamationPlan:
    """
    Which resting buys to cancel to fund a better one.

    Only same-symbol buys the caller rates weaker than the candidate are considered, so this is a
    priority swap inside a ceiling the symbol already respects, not a way around it. Weakest go
    first and it stops as soon as enough is freed, so a small upgrade does not clear the book.

    The plan is returned, not carried out: cancelling belongs to the caller, through
    ``Broker.cancel_open_orders(symbol, side, order_slots)``.
    """
    if required_notional <= 0.0:
        return CashReclamationPlan()

    candidates = []
    for view in open_orders:
        if view.symbol != symbol or view.order.side is not SignalType.BUY:
            continue
        slot = (view.order.metadata or {}).get(metadata_keys.ORDER_SLOT)
        if slot is None or strength(view) >= candidate_strength:
            continue
        price = view.limit_price or view.order.ref_price
        notional = max(0.0, float(view.remaining_quantity)) * max(0.0, float(price or 0.0))
        candidates.append((strength(view), str(slot), notional))

    slots: list[str] = []
    reclaimed = 0.0
    for _, slot, notional in sorted(candidates):
        slots.append(slot)
        reclaimed += notional
        if reclaimed >= required_notional:
            break
    if reclaimed < required_notional:
        return CashReclamationPlan()
    return CashReclamationPlan(order_slots=tuple(slots), reclaimed_notional=reclaimed)


def _view_bucket(view: OpenOrderView) -> str:
    bucket = (view.order.metadata or {}).get(metadata_keys.ALLOCATION_BUCKET)
    return DEFAULT_BUCKET if bucket is None else str(bucket)
