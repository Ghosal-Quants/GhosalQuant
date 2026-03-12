from datetime import datetime, timedelta, timezone
from itertools import count
import random

from .broker_base import Broker
from ..core.types import Fill, Order, SignalType, OpenOrderState


class SimBroker(Broker):
    """
    Simulated broker with fill modelling and latency
    """

    def __init__(
        self,
        *,
        spread_bps: float = 2.0,
        slippage_mean_bps: float = 0.0,
        slippage_std_bps: float = 1.0,
        commission_per_share: float = 0.0,
        partial_fill_probability: float = 0.0,
        reject_probability: float = 0.0,
        min_latency_ms: int = 0,
        max_latency_ms: int = 0,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.spread_bps = spread_bps
        self.slippage_mean_bps = slippage_mean_bps
        self.slippage_std_bps = slippage_std_bps
        self.commission_per_share = commission_per_share
        self.partial_fill_probability = partial_fill_probability
        self.reject_probability = reject_probability
        self.min_latency_ms = min_latency_ms
        self.max_latency_ms = max(min_latency_ms, max_latency_ms)

        self._rng = random.Random(seed)
        self._id_seq = count(1)
        self._open_orders: dict[str, OpenOrderState] = {}
        self._pending_fills: list[Fill] = []

    def submit(self, order: Order) -> str:
        order_id = f"SIM-{next(self._id_seq)}"
        self._open_orders[order_id] = OpenOrderState(
            broker_order_id=order_id,
            order=order,
            submitted_at=datetime.now(timezone.utc),
            remaining_qty=order.quantity,
        )
        return order_id

    def get_fills(self) -> list[Fill]:
        now = datetime.now(timezone.utc)

        for order_id, state in list(self._open_orders.items()):
            if not self._latency_elapsed(state.submitted_at, now):
                continue

            if self._rng.random() < self.reject_probability:
                del self._open_orders[order_id]
                continue

            fill_qty = self._decide_fill_qty(state.remaining_qty)
            if fill_qty <= 0:
                continue

            fill_price = self._compute_fill_price(state.order)

            self._pending_fills.append(Fill(
                time_stamp=now,
                symbol=state.order.symbol,
                side=state.order.side,
                quantity=fill_qty,
                price=fill_price,
                broker_order_id=order_id,
                commission=fill_qty * self.commission_per_share if self.commission_per_share else None,
            ))

            state.remaining_qty -= fill_qty
            if state.remaining_qty <= 0:
                del self._open_orders[order_id]

        fills = self._pending_fills
        self._pending_fills = []
        return fills

    def _latency_elapsed(self, submitted_at: datetime, now: datetime) -> bool:
        if self.max_latency_ms <= 0:
            return True
        latency = self._rng.randint(self.min_latency_ms, self.max_latency_ms)
        return now >= submitted_at + timedelta(milliseconds=latency)

    def _decide_fill_qty(self, remaining: float) -> float:
        if self._rng.random() < self.partial_fill_probability:
            return remaining * self._rng.uniform(0.1, 0.9)
        return remaining

    def _compute_fill_price(self, order: Order) -> float:
        base = order.ref_price

        side_sign = 1.0 if order.side == SignalType.BUY else -1.0

        # adverse spreads
        half_spread_frac = (self.spread_bps / 2.0) / 10_000

        # random: gaussian centred on slippage_mean_bps
        slip_bps = self._rng.gauss(self.slippage_mean_bps, self.slippage_std_bps)
        slip_frac = slip_bps / 10_000

        return base * (1.0 + side_sign * (half_spread_frac + slip_frac))
