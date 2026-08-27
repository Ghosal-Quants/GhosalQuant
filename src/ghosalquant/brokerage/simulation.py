from datetime import datetime, timedelta
from itertools import count
from typing import Iterable
import heapq
import random

from .broker_base import Broker
from .commissions import CommissionModel, PerShareCommission
from .fills import FillModel, PlannedFill, RestingOrder, SimulationContext
from ..core import metadata_keys
from ..core.types import Bar, Fill, Liquidity, MarketEvent, OpenOrderView, Order, OrderBook, PublicTrade, SignalType

_EPS = 1e-9


class SimulatedBroker(Broker):
    """
    A simulated broker that owns the order lifecycle and delegates microstructure to a
    ``FillModel``.

    The broker handles submission, latency, rejection, the working-order registry, time-to-live
    expiry, cancellation, commission and fill delivery. What a resting order actually fills
    against a bar, a book or a print is entirely the fill model's decision, so swapping
    ``BarParticipationFill`` for ``QueueAwareFill`` changes the market model without changing any
    of the surrounding behaviour.
    """

    def __init__(
        self,
        fill_model: FillModel,
        *,
        commission: CommissionModel | None = None,
        min_latency_ms: int = 0,
        max_latency_ms: int = 0,
        reject_probability: float = 0.0,
        max_order_age_events: int | None = None,
        max_order_age_seconds: float | None = None,
        fill_without_market_data: bool = False,
        order_id_prefix: str = "SIM",
        seed: int | None = None,
    ) -> None:
        super().__init__()
        if not 0.0 <= reject_probability <= 1.0:
            raise ValueError("reject_probability must be between 0.0 and 1.0")
        if max_order_age_events is not None and max_order_age_events <= 0:
            raise ValueError("max_order_age_events must be > 0 when set")
        if max_order_age_seconds is not None and max_order_age_seconds <= 0:
            raise ValueError("max_order_age_seconds must be > 0 when set")

        self.fill_model = fill_model
        self.commission = commission if commission is not None else PerShareCommission()
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max(min_latency_ms, max_latency_ms)
        self.reject_probability = reject_probability
        self.max_order_age_events = max_order_age_events
        self.max_order_age_seconds = max_order_age_seconds
        self.fill_without_market_data = fill_without_market_data
        self.order_id_prefix = order_id_prefix

        self._rng = random.Random(seed)
        self._id_seq = count(1)
        self._expiry_seq = count(1)
        self._orders: dict[str, RestingOrder] = {}
        self._ids_by_symbol: dict[str, dict[str, None]] = {}
        self._expiry_heap: list[tuple[datetime, int, str]] = []
        self._pending_fills: list[Fill] = []
        self._now: datetime | None = None
        self._has_market_data = False

    # ----- Market data ----- #

    def on_bar(self, bar: Bar) -> None:
        self._handle(bar)

    def on_order_book(self, book: OrderBook) -> OrderBook:
        return self._handle(book)

    def on_public_trade(self, trade: PublicTrade) -> None:
        self._handle(trade)

    def _handle(self, event: MarketEvent) -> MarketEvent:
        self._has_market_data = True
        self._now = event.time_stamp
        self.fill_model.observe(event)
        self.expire_orders(now=event.time_stamp, symbol=event.symbol)

        candidates = sorted(self._orders_for(event.symbol), key=lambda item: self.fill_model.priority(item[1]))
        for order_id, resting in candidates:
            if order_id not in self._orders:
                continue
            if event.time_stamp < resting.eligible_at:
                continue
            if resting.reject_on_eligibility:
                self._remove(order_id)
                continue

            resting.age_events += 1
            context = self._context(event.symbol)
            self._apply(resting, self.fill_model.fills_for(resting, self.fill_model.view(event), context))
            if self._finished(resting):
                self._remove(order_id)
            elif self.max_order_age_events is not None and resting.age_events >= self.max_order_age_events:
                self._remove(order_id)

        return self.fill_model.view(event)

    # ----- Execution ----- #

    def submit(self, order: Order) -> str:
        order_id = f"{self.order_id_prefix}-{next(self._id_seq)}"
        submitted_at = self._now or order.time_stamp
        resting = RestingOrder(
            broker_order_id=order_id,
            order=order,
            limit_price=order.declared_limit_price,
            remaining_quantity=float(order.quantity),
            liquidity=_liquidity(order),
            submitted_at=submitted_at,
            eligible_at=submitted_at + timedelta(milliseconds=self._latency_ms()),
            reject_on_eligibility=self._rng.random() < self.reject_probability,
        )

        self._apply(resting, self.fill_model.on_submitted(resting, self._context(order.symbol)))
        if self._finished(resting) or not self.fill_model.rests(resting):
            return order_id

        self._register(order_id, resting)
        return order_id

    def get_fills(self) -> list[Fill]:
        if not self._has_market_data and self.fill_without_market_data:
            self._fill_without_market_data()
        fills = self._pending_fills
        self._pending_fills = []
        return fills

    def _fill_without_market_data(self) -> None:
        for order_id, resting in list(self._orders.items()):
            now = _now_like(resting.eligible_at)
            if now < resting.eligible_at:
                continue
            if resting.reject_on_eligibility:
                self._remove(order_id)
                continue
            context = self._context(resting.symbol, now=now)
            self._apply(resting, self.fill_model.fills_without_market_data(resting, context))
            self._remove(order_id)

    def _apply(self, resting: RestingOrder, planned: Iterable[PlannedFill]) -> None:
        for fill in planned:
            if fill.quantity <= _EPS:
                continue
            # The realised liquidity is the model's to report; a bar model cannot know it.
            liquidity = fill.liquidity
            self._pending_fills.append(
                Fill(
                    time_stamp=self._now or resting.submitted_at,
                    symbol=resting.symbol,
                    side=resting.side,
                    quantity=fill.quantity,
                    price=fill.price,
                    broker_order_id=resting.broker_order_id,
                    commission=self.commission.charge(
                        price=fill.price, quantity=fill.quantity, liquidity=liquidity
                    ),
                    liquidity=liquidity,
                )
            )
            resting.remaining_quantity = max(0.0, resting.remaining_quantity - fill.quantity)

    def _finished(self, resting: RestingOrder) -> bool:
        if resting.remaining_quantity <= _EPS:
            return True
        metadata = resting.order.metadata or {}
        return bool(metadata.get(metadata_keys.CANCEL_REMAINDER_AFTER_FIRST_FILL)) and (
            resting.remaining_quantity < float(resting.order.quantity)
        )

    # ----- Order lifecycle ----- #

    def expire_orders(
        self,
        *,
        now: datetime,
        symbol: str | None = None,
        side: SignalType | None = None,
    ) -> list[str]:
        """Expire resting orders past their time to live, driven by a clock rather than by data."""
        expired: list[str] = []
        candidates = self._orders_for(symbol) if symbol is not None else list(self._orders.items())
        for order_id, resting in candidates:
            if side is not None and resting.side is not side:
                continue
            expires_at = self._expires_at(resting)
            if expires_at is not None and now >= expires_at:
                expired.append(order_id)
                self._remove(order_id)
        return expired

    def next_order_expiry_at(self) -> datetime | None:
        while self._expiry_heap:
            expires_at, _, order_id = self._expiry_heap[0]
            resting = self._orders.get(order_id)
            if resting is not None and self._expires_at(resting) == expires_at:
                return expires_at
            heapq.heappop(self._expiry_heap)
        return None

    def order_symbols_expiring_by(self, until: datetime) -> set[str]:
        return {
            resting.symbol
            for resting in self._orders.values()
            if (expires_at := self._expires_at(resting)) is not None and expires_at <= until
        }

    # ----- OpenOrderQueries ----- #

    def open_order_views(self, symbol: str | None = None) -> tuple[OpenOrderView, ...]:
        return tuple(
            OpenOrderView(
                broker_order_id=resting.broker_order_id,
                order=resting.order,
                remaining_quantity=resting.remaining_quantity,
                submitted_at=resting.submitted_at,
                limit_price=resting.limit_price,
                liquidity=resting.liquidity,
                queue_ahead_quantity=resting.queue_ahead_quantity,
            )
            for resting in self._orders.values()
            if symbol is None or resting.symbol == symbol
        )

    def cancel_open_orders(
        self,
        symbol: str,
        side: SignalType | None = None,
        order_slots: Iterable[str] | None = None,
    ) -> int:
        slots = None if order_slots is None else set(order_slots)
        cancelled = 0
        for order_id, resting in self._orders_for(symbol):
            if side is not None and resting.side is not side:
                continue
            if slots is not None and _order_slot(resting.order) not in slots:
                continue
            self._remove(order_id)
            cancelled += 1
        return cancelled

    # ----- Internals ----- #

    def _context(self, symbol: str, *, now: datetime | None = None) -> SimulationContext:
        book_for = getattr(self.fill_model, "book_for", None)
        return SimulationContext(
            book=book_for(symbol) if callable(book_for) else None,
            now=now if now is not None else self._now,
            rng=self._rng,
        )

    def _latency_ms(self) -> int:
        if self.max_latency_ms <= 0:
            return 0
        return self._rng.randint(self.min_latency_ms, self.max_latency_ms)

    def _register(self, order_id: str, resting: RestingOrder) -> None:
        self._orders[order_id] = resting
        self._ids_by_symbol.setdefault(resting.symbol, {})[order_id] = None
        expires_at = self._expires_at(resting)
        if expires_at is not None:
            heapq.heappush(self._expiry_heap, (expires_at, next(self._expiry_seq), order_id))

    def _remove(self, order_id: str) -> RestingOrder | None:
        resting = self._orders.pop(order_id, None)
        if resting is None:
            return None
        by_symbol = self._ids_by_symbol.get(resting.symbol)
        if by_symbol is not None:
            by_symbol.pop(order_id, None)
            if not by_symbol:
                self._ids_by_symbol.pop(resting.symbol, None)
        return resting

    def _orders_for(self, symbol: str) -> list[tuple[str, RestingOrder]]:
        return [
            (order_id, self._orders[order_id])
            for order_id in self._ids_by_symbol.get(symbol, ())
            if order_id in self._orders
        ]

    def _expires_at(self, resting: RestingOrder) -> datetime | None:
        metadata = resting.order.metadata or {}
        value = metadata.get(metadata_keys.MAX_ORDER_AGE_SECONDS)
        max_age = float(value) if value is not None and float(value) > 0.0 else self.max_order_age_seconds
        if max_age is None:
            return None
        return resting.order.time_stamp + timedelta(seconds=max_age)


def _liquidity(order: Order) -> Liquidity:
    if order.liquidity is not None:
        return order.liquidity
    metadata = order.metadata or {}
    value = metadata.get(metadata_keys.LIQUIDITY)
    return Liquidity(str(value).lower()) if value is not None else Liquidity.MAKER


def _order_slot(order: Order) -> str | None:
    metadata = order.metadata or {}
    slot = metadata.get(metadata_keys.ORDER_SLOT)
    return None if slot is None else str(slot)


def _now_like(reference: datetime) -> datetime:
    return datetime.now() if reference.tzinfo is None else datetime.now(reference.tzinfo)
