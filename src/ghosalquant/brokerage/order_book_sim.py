from ..core.types import OrderBook
from .commissions import MakerTakerCommission
from .fills import QueueAwareFill
from .simulation import SimulatedBroker


class OrderBookSimBroker(SimulatedBroker):
    """
    Queue-aware simulated broker for L2 order books.

    A preset of ``SimulatedBroker`` wired to ``QueueAwareFill``: a taker sweeps the displayed book
    immediately and never rests, while a maker rests behind the size already showing at its price
    and fills only when a public print trades through that price and consumes the queue ahead of
    it, or when the book itself crosses the order's limit. Depth this broker consumes is withheld
    from the book handed to the strategy until the venue redisplays it.

    ``maker_rebate_bps`` is credited (a negative ``Fill.commission``) and ``taker_fee_bps`` is
    charged. Both are required: passive P&L is mostly fee economics, so the venue's schedule is
    not something to inherit from a default.
    """

    def __init__(
        self,
        *,
        maker_rebate_bps: float,
        taker_fee_bps: float,
        public_trade_participation: float = 1.0,
        lot_size: float | None = None,
        model_queue_position: bool = True,
        min_latency_ms: int = 0,
        max_latency_ms: int = 0,
        reject_probability: float = 0.0,
        max_order_age_events: int | None = None,
        max_order_age_seconds: float | None = None,
        seed: int | None = None,
    ) -> None:
        super().__init__(
            QueueAwareFill(
                public_trade_participation=public_trade_participation,
                lot_size=lot_size,
                model_queue_position=model_queue_position,
            ),
            commission=MakerTakerCommission(maker_rebate_bps=maker_rebate_bps, taker_fee_bps=taker_fee_bps),
            min_latency_ms=min_latency_ms,
            max_latency_ms=max_latency_ms,
            reject_probability=reject_probability,
            max_order_age_events=max_order_age_events,
            max_order_age_seconds=max_order_age_seconds,
            order_id_prefix="OBS",
            seed=seed,
        )

    @property
    def maker_rebate_bps(self) -> float:
        return self.commission.maker_rebate_bps

    @property
    def taker_fee_bps(self) -> float:
        return self.commission.taker_fee_bps

    @property
    def public_trade_participation(self) -> float:
        return self.fill_model.public_trade_participation

    @property
    def lot_size(self) -> float | None:
        return self.fill_model.lot_size

    def adjusted_book(self, book: OrderBook) -> OrderBook:
        """The book with this broker's own consumed depth removed."""
        return self.fill_model.adjusted(book)

    def observe_unchanged_book(self, book: OrderBook) -> OrderBook:
        """Record a book, and the time it was seen, without re-running resting-order matching."""
        self.fill_model.note_book(book)
        self._now = book.time_stamp if self._now is None else max(self._now, book.time_stamp)
        return self.fill_model.adjusted(book)
