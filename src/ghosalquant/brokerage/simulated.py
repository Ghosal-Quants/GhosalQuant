from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count
import math
import random

from .broker_base import Broker
from ..core import metadata_keys
from ..core.types import Bar, BookLevel, Fill, OpenOrderView, Order, OrderBook, PublicTrade, SignalType


@dataclass(slots=True)
class SimOpenOrderState:
    broker_order_id: str
    order: Order
    submitted_at: datetime
    eligible_at: datetime
    remaining_qty: float
    reject_on_eligibility: bool


class SimBroker(Broker):
    """
    Bar-driven simulated broker with latency, liquidity caps, and fill modelling.

    Order books are also accepted: an eligible order crosses the touch (BUY at the best ask, SELL
    at the best bid) for up to the displayed quantity at that level, and an order whose limit the
    touch does not reach rests. This is deliberately naive — no queue position, no participation
    cap, no depth sweeping — and exists so a book-only run fills something.
    """

    _QTY_EPSILON = 1e-9

    def __init__(
        self,
        *,
        spread_bps: float = 2.0,
        slippage_mean_bps: float = 0.0,
        slippage_std_bps: float = 1.0,
        commission_per_share: float = 0.0,
        partial_fill_probability: float = 0.0,
        reject_probability: float = 0.0,
        max_fill_pct_of_bar_volume: float = 0.25,
        impact_bps_at_full_participation: float = 10.0,
        min_latency_ms: int = 0,
        max_latency_ms: int = 0,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self._validate_probability("partial_fill_probability", partial_fill_probability)
        self._validate_probability("reject_probability", reject_probability)
        if not 0.0 <= max_fill_pct_of_bar_volume <= 1.0:
            raise ValueError("max_fill_pct_of_bar_volume must be between 0.0 and 1.0")
        if impact_bps_at_full_participation < 0.0:
            raise ValueError("impact_bps_at_full_participation must be >= 0.0")

        self.spread_bps = spread_bps
        self.slippage_mean_bps = slippage_mean_bps
        self.slippage_std_bps = slippage_std_bps
        self.commission_per_share = commission_per_share
        self.partial_fill_probability = partial_fill_probability
        self.reject_probability = reject_probability
        self.max_fill_pct_of_bar_volume = max_fill_pct_of_bar_volume
        self.impact_bps_at_full_participation = impact_bps_at_full_participation
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max(min_latency_ms, max_latency_ms)

        self._rng = random.Random(seed)
        self._id_seq = count(1)
        self._open_orders: dict[str, SimOpenOrderState] = {}
        self._pending_fills: list[Fill] = []
        self._current_market_time: datetime | None = None
        self._has_market_data = False

    def submit(self, order: Order) -> str:
        order_id = f"SIM-{next(self._id_seq)}"
        submitted_at = self._current_market_time or order.time_stamp
        latency_ms = self._sample_latency_ms()

        self._open_orders[order_id] = SimOpenOrderState(
            broker_order_id=order_id,
            order=order,
            submitted_at=submitted_at,
            eligible_at=submitted_at + timedelta(milliseconds=latency_ms),
            remaining_qty=order.quantity,
            reject_on_eligibility=self._rng.random() < self.reject_probability,
        )
        return order_id

    def on_bar(self, bar: Bar) -> None:
        self._has_market_data = True
        self._current_market_time = bar.time_stamp
        self._process_bar(bar)

    def on_order_book(self, book: OrderBook) -> OrderBook:
        self._has_market_data = True
        self._current_market_time = book.time_stamp
        self._process_book(book)
        return book

    def on_public_trade(self, trade: PublicTrade) -> None:
        self._has_market_data = True
        self._current_market_time = trade.time_stamp

    def get_fills(self) -> list[Fill]:
        if not self._has_market_data:
            self._process_wall_clock_orders()

        fills = self._pending_fills
        self._pending_fills = []
        return fills

    # ----- OpenOrderQueries ----- #
    def open_order_views(self, symbol: str | None = None) -> tuple[OpenOrderView, ...]:
        views: list[OpenOrderView] = []
        for state in self._open_orders.values():
            if symbol is not None and state.order.symbol != symbol:
                continue
            views.append(
                OpenOrderView(
                    broker_order_id=state.broker_order_id,
                    order=state.order,
                    remaining_quantity=state.remaining_qty,
                    submitted_at=state.submitted_at,
                    limit_price=self._order_limit(state.order),
                )
            )
        return tuple(views)

    def cancel_open_orders(self, symbol: str, side: SignalType | None = None) -> int:
        cancelled = 0
        for order_id, state in list(self._open_orders.items()):
            if state.order.symbol != symbol:
                continue
            if side is not None and state.order.side is not side:
                continue
            del self._open_orders[order_id]
            cancelled += 1
        return cancelled

    # ----- Fill models ----- #
    def _process_bar(self, bar: Bar) -> None:
        for order_id, state in list(self._open_orders.items()):
            if state.order.symbol != bar.symbol:
                continue
            if not self._time_reached(bar.time_stamp, state.eligible_at):
                continue
            if state.reject_on_eligibility:
                del self._open_orders[order_id]
                continue

            fill_qty = self._decide_fill_qty(state.remaining_qty, bar.volume)
            if fill_qty <= self._QTY_EPSILON:
                continue

            self._pending_fills.append(
                Fill(
                    time_stamp=bar.time_stamp,
                    symbol=state.order.symbol,
                    side=state.order.side,
                    quantity=fill_qty,
                    price=self._compute_fill_price(state.order, bar, fill_qty),
                    broker_order_id=order_id,
                    commission=self._compute_commission(fill_qty),
                )
            )

            state.remaining_qty = max(0.0, state.remaining_qty - fill_qty)
            if state.remaining_qty <= self._QTY_EPSILON:
                del self._open_orders[order_id]

    def _process_book(self, book: OrderBook) -> None:
        for order_id, state in list(self._open_orders.items()):
            if state.order.symbol != book.symbol:
                continue
            if not self._time_reached(book.time_stamp, state.eligible_at):
                continue
            if state.reject_on_eligibility:
                del self._open_orders[order_id]
                continue

            touch = self._touch_level(state.order, book)
            if touch is None:
                continue

            fill_qty = min(state.remaining_qty, float(touch.quantity))
            if fill_qty <= self._QTY_EPSILON:
                continue

            self._pending_fills.append(
                Fill(
                    time_stamp=book.time_stamp,
                    symbol=state.order.symbol,
                    side=state.order.side,
                    quantity=fill_qty,
                    price=float(touch.price),
                    broker_order_id=order_id,
                    commission=self._compute_commission(fill_qty),
                )
            )

            state.remaining_qty = max(0.0, state.remaining_qty - fill_qty)
            if state.remaining_qty <= self._QTY_EPSILON:
                del self._open_orders[order_id]

    def _touch_level(self, order: Order, book: OrderBook) -> BookLevel | None:
        """The level an order would cross at, or None when it rests (no touch, or limit not reached)."""
        limit = self._order_limit(order)
        if order.side is SignalType.BUY:
            level = book.best_ask
            if level is None or (limit is not None and level.price > limit):
                return None
            return level
        level = book.best_bid
        if level is None or (limit is not None and level.price < limit):
            return None
        return level

    @staticmethod
    def _order_limit(order: Order) -> float | None:
        if order.metadata is None:
            return None
        value = order.metadata.get(metadata_keys.LIMIT_PRICE)
        return None if value is None else float(value)

    def _process_wall_clock_orders(self) -> None:
        for order_id, state in list(self._open_orders.items()):
            now = self._now_like(state.eligible_at)
            if not self._time_reached(now, state.eligible_at):
                continue
            if state.reject_on_eligibility:
                del self._open_orders[order_id]
                continue

            fill_qty = state.remaining_qty
            if fill_qty <= self._QTY_EPSILON:
                del self._open_orders[order_id]
                continue

            self._pending_fills.append(
                Fill(
                    time_stamp=now,
                    symbol=state.order.symbol,
                    side=state.order.side,
                    quantity=fill_qty,
                    price=self._compute_reference_fill_price(state.order),
                    broker_order_id=order_id,
                    commission=self._compute_commission(fill_qty),
                )
            )
            del self._open_orders[order_id]

    def _sample_latency_ms(self) -> int:
        if self.max_latency_ms <= 0:
            return 0
        return self._rng.randint(self.min_latency_ms, self.max_latency_ms)

    def _decide_fill_qty(self, remaining: float, bar_volume: float) -> float:
        if remaining <= self._QTY_EPSILON:
            return 0.0
        if bar_volume <= 0.0 or self.max_fill_pct_of_bar_volume <= 0.0:
            return 0.0

        fill_qty = min(remaining, float(bar_volume) * self.max_fill_pct_of_bar_volume)
        if fill_qty <= self._QTY_EPSILON:
            return 0.0
        if self.partial_fill_probability > 0.0 and self._rng.random() < self.partial_fill_probability:
            fill_qty *= self._rng.uniform(0.25, 0.9)
        return min(fill_qty, remaining)

    def _compute_fill_price(self, order: Order, bar: Bar, fill_qty: float) -> float:
        base = float(bar.open)
        volume = max(float(bar.volume), fill_qty)
        participation = min(1.0, fill_qty / volume)

        side_sign = 1.0 if order.side == SignalType.BUY else -1.0

        half_spread_bps = self.spread_bps / 2.0
        slip_bps = self._rng.gauss(self.slippage_mean_bps, self.slippage_std_bps)
        impact_bps = self.impact_bps_at_full_participation * math.sqrt(participation)
        adjusted_price = base * (1.0 + side_sign * ((half_spread_bps + slip_bps + impact_bps) / 10_000.0))

        low = float(bar.low)
        high = float(bar.high)
        if order.side == SignalType.BUY:
            return min(max(adjusted_price, low), high)
        return max(min(adjusted_price, high), low)

    def _compute_reference_fill_price(self, order: Order) -> float:
        side_sign = 1.0 if order.side == SignalType.BUY else -1.0
        half_spread_bps = self.spread_bps / 2.0
        slip_bps = self._rng.gauss(self.slippage_mean_bps, self.slippage_std_bps)
        return float(order.ref_price) * (1.0 + side_sign * ((half_spread_bps + slip_bps) / 10_000.0))

    def _compute_commission(self, fill_qty: float) -> float | None:
        if not self.commission_per_share:
            return None
        return fill_qty * self.commission_per_share

    @staticmethod
    def _validate_probability(name: str, value: float) -> None:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"{name} must be between 0.0 and 1.0")

    @staticmethod
    def _now_like(reference: datetime) -> datetime:
        if reference.tzinfo is None:
            return datetime.now()
        return datetime.now(reference.tzinfo)

    @staticmethod
    def _time_reached(current_time: datetime, target_time: datetime) -> bool:
        if (current_time.tzinfo is None) != (target_time.tzinfo is None):
            raise ValueError("Cannot compare naive and timezone-aware datetimes")
        return current_time >= target_time
