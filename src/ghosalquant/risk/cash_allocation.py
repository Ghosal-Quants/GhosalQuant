from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
import logging

from ..core import metadata_keys
from ..core.contracts import ContractRegistry
from ..core.types import OpenOrderView, SignalType
from .sizing import sizing_capital
from .types import OrderDraft, RiskContext, RiskRule

logger = logging.getLogger(__name__)

DEFAULT_BUCKET = "default"
_EPS = 1e-9

ALLOCATION_BUCKET = "cash_allocation_bucket"
ALLOCATION_CAPITAL = "cash_allocation_capital"
ALLOCATION_HEADROOM = "cash_allocation_headroom_notional"
ALLOCATION_CAPPED_FROM = "cash_allocation_capped_from_quantity"

Classifier = Callable[[SignalType, Mapping[str, Any]], str]


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
    ``priority_total_fraction`` lets the orders a caller marks as priority use more of the account
    than the rest may.
    """

    total_fraction: float = 0.9
    max_symbol_fraction: float = 0.3
    bucket_fractions: Mapping[str, float] = field(default_factory=dict)
    max_order_fractions: Mapping[str, float] = field(default_factory=dict)
    default_bucket_fraction: float = 1.0
    default_max_order_fraction: float = 1.0
    priority_total_fraction: float | None = None

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
        if self.priority_total_fraction is not None and not (
            self.total_fraction <= self.priority_total_fraction <= 1.0
        ):
            raise ValueError("priority_total_fraction must be in [total_fraction, 1]")

    def total(self, priority: bool = False) -> float:
        if priority and self.priority_total_fraction is not None:
            return float(self.priority_total_fraction)
        return float(self.total_fraction)

    def bucket_fraction(self, bucket: str) -> float:
        return float(self.bucket_fractions.get(bucket, self.default_bucket_fraction))

    def max_order_fraction(self, bucket: str) -> float:
        return float(self.max_order_fractions.get(bucket, self.default_max_order_fraction))


@dataclass(frozen=True, slots=True)
class CashCeilings:
    """The four ceilings on one order's resting buys, and what already rests under each."""

    total: float
    total_resting: float
    symbol: float
    symbol_resting: float
    bucket: float
    bucket_resting: float
    order: float

    @property
    def headroom(self) -> float:
        """What the order may still commit: the tightest ceiling, never below zero."""
        return min(
            max(0.0, self.total - self.total_resting),
            max(0.0, self.symbol - self.symbol_resting),
            max(0.0, self.bucket - self.bucket_resting),
            max(0.0, self.order),
        )


@dataclass(frozen=True, slots=True)
class CashReclamationPlan:
    """Which resting orders to cancel, what cancelling them frees, and what was asked for."""

    order_slots: tuple[str, ...] = ()
    reclaimed_notional: float = 0.0
    required_notional: float = 0.0


def bucket_of(side: SignalType, metadata: Mapping[str, Any] | None) -> str:
    """The default classifier: whatever the order named, else one shared bucket."""
    bucket = (metadata or {}).get(metadata_keys.ALLOCATION_BUCKET)
    return DEFAULT_BUCKET if bucket is None else str(bucket)


def resting_buy_notional(
    open_orders: Iterable[OpenOrderView],
    *,
    symbol: str | None = None,
    bucket: str | None = None,
    classify: Classifier = bucket_of,
    contracts: ContractRegistry | None = None,
) -> float:
    """What the resting buys already reserve, optionally for one symbol or one bucket."""
    registry = contracts if contracts is not None else ContractRegistry()
    total = 0.0
    for view in open_orders:
        if view.order.side is not SignalType.BUY:
            continue
        if symbol is not None and view.symbol != symbol:
            continue
        if bucket is not None and classify(view.side, view.order.metadata or {}) != bucket:
            continue
        total += _resting_notional(view, registry)
    return total


def cash_ceilings(
    config: CashAllocationConfig,
    *,
    capital: float,
    symbol: str,
    bucket: str,
    open_orders: Iterable[OpenOrderView],
    classify: Classifier = bucket_of,
    priority: bool = False,
    contracts: ContractRegistry | None = None,
) -> CashCeilings:
    """The ceilings a buy of ``symbol`` in ``bucket`` meets, given the resting orders."""
    orders = tuple(open_orders)
    return CashCeilings(
        total=capital * config.total(priority),
        total_resting=resting_buy_notional(orders, contracts=contracts),
        symbol=capital * config.max_symbol_fraction,
        symbol_resting=resting_buy_notional(orders, symbol=symbol, contracts=contracts),
        bucket=capital * config.bucket_fraction(bucket),
        bucket_resting=resting_buy_notional(orders, bucket=bucket, classify=classify, contracts=contracts),
        order=capital * config.max_order_fraction(bucket),
    )


class CashAllocationRule(RiskRule):
    """
    Keep dry powder: trim a buy to what the account can still commit to resting orders.

    Four ceilings apply at once — the account total, this symbol, this bucket, and this one order
    — and the draft is trimmed to the tightest of them. Anything with no headroom left is blocked
    rather than sent, since the cash behind it is already spoken for. ``classify`` buckets new and
    resting orders alike; ``is_priority`` picks the orders the priority total applies to.
    """

    def __init__(
        self,
        config: CashAllocationConfig,
        *,
        capital: float,
        classify: Classifier = bucket_of,
        is_priority: Callable[[SignalType, Mapping[str, Any]], bool] | None = None,
        reference_capital: float | None = None,
    ) -> None:
        self.config = config
        self.capital = float(capital)
        self.classify = classify
        self.is_priority = is_priority
        self.reference_capital = reference_capital
        sizing_capital(RiskContext(), capital=self.capital, reference_capital=reference_capital)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side is not SignalType.BUY or draft.quantity is None or draft.price <= 0.0:
            return True

        capital = sizing_capital(
            context, capital=self.capital, reference_capital=self.reference_capital
        )
        bucket = self.classify(draft.side, draft.metadata)
        headroom = cash_ceilings(
            self.config,
            capital=capital,
            symbol=draft.symbol,
            bucket=bucket,
            open_orders=context.open_orders,
            classify=self.classify,
            priority=self.is_priority is not None and self.is_priority(draft.side, draft.metadata),
            contracts=context.contracts,
        ).headroom
        draft.metadata.update({
            ALLOCATION_BUCKET: bucket,
            ALLOCATION_CAPITAL: capital,
            ALLOCATION_HEADROOM: headroom,
        })

        if headroom <= 0.0:
            logger.info("CashAllocation blocked BUY %s: no headroom in %s", draft.symbol, bucket)
            return False

        allowed = headroom / (float(draft.price) * context.contracts.get(draft.symbol).multiplier)
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
    eligible: Callable[[OpenOrderView], bool],
    weakest_first: Callable[[OpenOrderView], Any],
    allow_partial: bool = False,
    contracts: ContractRegistry | None = None,
) -> CashReclamationPlan:
    """
    Which resting buys to cancel to fund a better one.

    Only same-symbol buys with an ``ORDER_SLOT`` that ``eligible`` admits are considered, so this
    is a priority swap inside a ceiling the symbol already respects, not a way around it. They go
    in ``weakest_first`` order and it stops as soon as enough is freed, so a small upgrade does not
    clear the book. A plan that cannot free enough is empty unless ``allow_partial``.

    The plan is returned, not carried out: cancelling belongs to the caller, through
    ``Broker.cancel_open_orders(symbol, side, order_slots)``.
    """
    if required_notional <= _EPS:
        return CashReclamationPlan()

    registry = contracts if contracts is not None else ContractRegistry()
    candidates = sorted(
        (
            view for view in open_orders
            if view.symbol == symbol
            and view.order.side is SignalType.BUY
            and view.order_slot is not None
            and eligible(view)
        ),
        key=weakest_first,
    )
    slots: list[str] = []
    reclaimed = 0.0
    for view in candidates:
        if reclaimed + _EPS >= required_notional:
            break
        if view.order_slot in slots:
            continue
        slots.append(view.order_slot)
        reclaimed += _resting_notional(view, registry)
    if not slots or (required_notional - reclaimed > _EPS and not allow_partial):
        return CashReclamationPlan(required_notional=required_notional)
    return CashReclamationPlan(
        order_slots=tuple(slots), reclaimed_notional=reclaimed, required_notional=required_notional
    )


def _resting_notional(view: OpenOrderView, contracts: ContractRegistry) -> float:
    price = view.limit_price or view.order.ref_price
    unit = max(0.0, float(price or 0.0)) * contracts.get(view.symbol).multiplier
    return max(0.0, float(view.remaining_quantity)) * unit
