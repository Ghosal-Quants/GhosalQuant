from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping, Protocol, Sequence
import math
import random

from ..core import metadata_keys
from ..core.types import Bar, BookLevel, Liquidity, MarketEvent, Order, OrderBook, PublicTrade, SignalType

_EPS = 1e-12


@dataclass(slots=True)
class RestingOrder:
    """
    A working order in a simulated broker.

    The record is a superset of what the fill models need: a bar model uses ``eligible_at`` and
    ``reject_on_eligibility``, a queue model uses ``queue_ahead_quantity``, and either may use
    ``age_events``. A model simply ignores the fields it has no use for.
    """
    broker_order_id: str
    order: Order
    limit_price: float | None
    remaining_quantity: float
    liquidity: Liquidity
    submitted_at: datetime
    eligible_at: datetime
    reject_on_eligibility: bool = False
    queue_ahead_quantity: float = 0.0
    age_events: int = 0
    sweep_pending: bool = False

    @property
    def symbol(self) -> str:
        return self.order.symbol

    @property
    def side(self) -> SignalType:
        return self.order.side


@dataclass(frozen=True, slots=True)
class PlannedFill:
    price: float
    quantity: float
    liquidity: Liquidity | None = None


@dataclass(slots=True)
class SimulationContext:
    """The market as the broker currently knows it, plus the broker's seeded randomness."""
    book: OrderBook | None
    now: datetime | None
    rng: random.Random


class FillModel(Protocol):
    """
    Decides what a resting order fills, given a market event. The broker owns everything else:
    order registry, latency, rejection, expiry, cancellation, commission and fill delivery.
    """

    def observe(self, event: MarketEvent) -> None:
        """Take the event into the model's own state."""
        ...

    def view(self, event: MarketEvent) -> MarketEvent:
        """What the strategy, and the next order to match, should see right now."""
        ...

    def priority(self, resting: RestingOrder) -> tuple:
        """Order in which resting orders get a chance at the same event."""
        ...

    def on_submitted(self, resting: RestingOrder, context: SimulationContext) -> Sequence[PlannedFill]:
        """Seed per-order state and optionally fill immediately."""
        ...

    def rests(self, resting: RestingOrder) -> bool:
        """Whether an unfilled order stays working after submission."""
        ...

    def fills_for(
        self, resting: RestingOrder, event: MarketEvent, context: SimulationContext
    ) -> Sequence[PlannedFill]:
        ...

    def fills_without_market_data(
        self, resting: RestingOrder, context: SimulationContext
    ) -> Sequence[PlannedFill]:
        """Fills for a broker that has never seen a market event."""
        ...


class BarParticipationFill:
    """
    Bar-driven fills: an eligible order takes a share of the bar's volume at a price built from
    the bar open plus half-spread, slippage and participation impact, clamped to the bar's range.
    A limit that the modelled price does not reach rests until a later bar.
    """

    def __init__(
        self,
        *,
        spread_bps: float = 2.0,
        slippage_mean_bps: float = 0.0,
        slippage_std_bps: float = 1.0,
        partial_fill_probability: float = 0.0,
        max_fill_pct_of_bar_volume: float = 0.25,
        impact_bps_at_full_participation: float = 10.0,
    ) -> None:
        if not 0.0 <= partial_fill_probability <= 1.0:
            raise ValueError("partial_fill_probability must be between 0.0 and 1.0")
        if not 0.0 <= max_fill_pct_of_bar_volume <= 1.0:
            raise ValueError("max_fill_pct_of_bar_volume must be between 0.0 and 1.0")
        if impact_bps_at_full_participation < 0.0:
            raise ValueError("impact_bps_at_full_participation must be >= 0.0")

        self.spread_bps = spread_bps
        self.slippage_mean_bps = slippage_mean_bps
        self.slippage_std_bps = slippage_std_bps
        self.partial_fill_probability = partial_fill_probability
        self.max_fill_pct_of_bar_volume = max_fill_pct_of_bar_volume
        self.impact_bps_at_full_participation = impact_bps_at_full_participation

    def observe(self, event: MarketEvent) -> None:
        return None

    def view(self, event: MarketEvent) -> MarketEvent:
        return event

    def priority(self, resting: RestingOrder) -> tuple:
        return (resting.submitted_at, resting.broker_order_id)

    def on_submitted(self, resting: RestingOrder, context: SimulationContext) -> Sequence[PlannedFill]:
        return ()

    def rests(self, resting: RestingOrder) -> bool:
        return True

    def fills_for(
        self, resting: RestingOrder, event: MarketEvent, context: SimulationContext
    ) -> Sequence[PlannedFill]:
        if not isinstance(event, Bar):
            return ()
        quantity = self._participation(resting.remaining_quantity, event.volume, context.rng)
        if quantity <= _EPS:
            return ()
        price = self._price(resting, event, quantity, context.rng)
        if not _reaches_limit(resting, price):
            return ()
        return (PlannedFill(price, quantity),)

    def fills_without_market_data(
        self, resting: RestingOrder, context: SimulationContext
    ) -> Sequence[PlannedFill]:
        side_sign = 1.0 if resting.side is SignalType.BUY else -1.0
        slip = context.rng.gauss(self.slippage_mean_bps, self.slippage_std_bps)
        price = float(resting.order.ref_price) * (
            1.0 + side_sign * ((self.spread_bps / 2.0 + slip) / 10_000.0)
        )
        return (PlannedFill(price, resting.remaining_quantity),)

    def _participation(self, remaining: float, volume: float, rng: random.Random) -> float:
        if remaining <= _EPS or volume <= 0.0 or self.max_fill_pct_of_bar_volume <= 0.0:
            return 0.0
        quantity = min(remaining, float(volume) * self.max_fill_pct_of_bar_volume)
        if quantity <= _EPS:
            return 0.0
        if self.partial_fill_probability > 0.0 and rng.random() < self.partial_fill_probability:
            quantity *= rng.uniform(0.25, 0.9)
        return min(quantity, remaining)

    def _price(self, resting: RestingOrder, bar: Bar, quantity: float, rng: random.Random) -> float:
        base = float(bar.open)
        volume = max(float(bar.volume), quantity)
        participation = min(1.0, quantity / volume)
        side_sign = 1.0 if resting.side is SignalType.BUY else -1.0
        slip = rng.gauss(self.slippage_mean_bps, self.slippage_std_bps)
        impact = self.impact_bps_at_full_participation * math.sqrt(participation)
        price = base * (1.0 + side_sign * ((self.spread_bps / 2.0 + slip + impact) / 10_000.0))
        low, high = float(bar.low), float(bar.high)
        return min(max(price, low), high) if resting.side is SignalType.BUY else max(min(price, high), low)


class QueueAwareFill:
    """
    Level-2 fills. A taker sweeps the displayed book immediately. A maker rests behind the size
    already showing at its price and fills only when a public print trades through that price and
    consumes the queue ahead of it, or when the book itself crosses the order's limit.

    Depth this model consumes is withheld from the book it returns from ``observe`` until the
    venue redisplays it, so the same liquidity cannot be taken twice.
    """

    def __init__(
        self,
        *,
        public_trade_participation: float = 1.0,
        lot_size: float | None = None,
        model_queue_position: bool = True,
    ) -> None:
        if not 0.0 < public_trade_participation <= 1.0:
            raise ValueError("public_trade_participation must be in (0, 1]")
        if lot_size is not None and lot_size <= 0.0:
            raise ValueError("lot_size must be > 0 when set")

        self.public_trade_participation = float(public_trade_participation)
        self.lot_size = lot_size
        self.model_queue_position = bool(model_queue_position)

        self._books: dict[str, OrderBook] = {}
        self._consumed: dict[tuple[str, str, float], float] = {}
        self._consumed_keys: dict[tuple[str, str], set[tuple[str, str, float]]] = {}
        self._last_displayed: dict[tuple[str, str, float], float] = {}
        self._displayed_keys: dict[str, set[tuple[str, str, float]]] = {}
        self._print_budget: dict[str, float] = {}
        self._seen_trades: set[str] = set()

    # ----- FillModel ----- #

    def observe(self, event: MarketEvent) -> None:
        if isinstance(event, OrderBook):
            self._reconcile(event)
            self._books[event.symbol] = event
        elif isinstance(event, PublicTrade):
            if event.trade_id is not None and event.trade_id in self._seen_trades:
                self._print_budget[event.symbol] = 0.0
                return
            if event.trade_id is not None:
                self._seen_trades.add(event.trade_id)
            traded = max(0.0, float(event.quantity))
            self._print_budget[event.symbol] = self._round_lot(traded * self.public_trade_participation)

    def view(self, event: MarketEvent) -> MarketEvent:
        return self.adjusted(event) if isinstance(event, OrderBook) else event

    def on_submitted(self, resting: RestingOrder, context: SimulationContext) -> Sequence[PlannedFill]:
        book = context.book
        if resting.liquidity is Liquidity.MAKER and self.model_queue_position:
            resting.queue_ahead_quantity = _displayed_at(book, resting.side, resting.limit_price)
        if book is not None and book.time_stamp >= resting.submitted_at:
            return self._against_book(resting, self.adjusted(book), aggressor=True)
        # No quote since the order was decided, so wait for the next one.
        resting.sweep_pending = resting.liquidity is Liquidity.TAKER
        return ()

    def rests(self, resting: RestingOrder) -> bool:
        return resting.liquidity is not Liquidity.TAKER or resting.sweep_pending

    def fills_for(
        self, resting: RestingOrder, event: MarketEvent, context: SimulationContext
    ) -> Sequence[PlannedFill]:
        if isinstance(event, OrderBook):
            if resting.sweep_pending:
                resting.sweep_pending = False
                return self._against_book(resting, event, aggressor=True)
            return self._against_book(resting, event, aggressor=False)
        if isinstance(event, PublicTrade):
            return self._against_print(resting, event)
        return ()

    def fills_without_market_data(
        self, resting: RestingOrder, context: SimulationContext
    ) -> Sequence[PlannedFill]:
        return ()

    # ----- Book access ----- #

    def book_for(self, symbol: str) -> OrderBook | None:
        return self._books.get(symbol)

    def note_book(self, book: OrderBook) -> None:
        """Record a book without matching orders against it."""
        self._books[book.symbol] = book

    def adjusted(self, book: OrderBook) -> OrderBook:
        bid_keys = self._consumed_keys.get((book.symbol, "bid"))
        ask_keys = self._consumed_keys.get((book.symbol, "ask"))
        if not bid_keys and not ask_keys:
            return book
        return book.replace(
            bids=_remaining(book.symbol, "bid", book.bids, self._consumed) if bid_keys else book.bids,
            asks=_remaining(book.symbol, "ask", book.asks, self._consumed) if ask_keys else book.asks,
        )

    # ----- Matching ----- #

    def _against_book(
        self, resting: RestingOrder, book: OrderBook, *, aggressor: bool
    ) -> Sequence[PlannedFill]:
        quantity = self._round_lot(resting.remaining_quantity)
        if quantity <= _EPS:
            return ()

        buying = resting.side is SignalType.BUY
        levels = book.asks if buying else book.bids
        side = "ask" if buying else "bid"
        taking = resting.liquidity is Liquidity.TAKER
        limit = resting.limit_price

        planned: list[PlannedFill] = []
        remaining = quantity
        for level in levels:
            if remaining <= _EPS:
                break
            if limit is not None and (level.price > limit if buying else level.price < limit):
                break
            fill_qty = self._round_lot(min(remaining, float(level.quantity)))
            if fill_qty <= _EPS:
                break
            # Whoever crosses pays the resting side's price: we take the level when we are the
            # aggressor, and keep our own limit when the book comes to a quote already resting.
            price = level.price if taking or aggressor or limit is None else limit
            planned.append(PlannedFill(price, fill_qty, resting.liquidity))
            self._consume(resting.symbol, side, level.price, fill_qty)
            remaining -= fill_qty
        return planned

    def _against_print(self, resting: RestingOrder, trade: PublicTrade) -> Sequence[PlannedFill]:
        if resting.liquidity is Liquidity.TAKER or trade.time_stamp < resting.submitted_at:
            return ()
        limit = resting.limit_price
        if limit is not None and (
            trade.price > limit if resting.side is SignalType.BUY else trade.price < limit
        ):
            return ()

        traded = max(0.0, float(trade.quantity))
        consumed = min(resting.queue_ahead_quantity, traded)
        resting.queue_ahead_quantity -= consumed
        traded -= consumed
        if resting.queue_ahead_quantity > _EPS or traded <= _EPS:
            return ()

        budget = self._print_budget.get(trade.symbol, 0.0)
        fill_qty = self._round_lot(min(resting.remaining_quantity, traded, budget))
        if fill_qty <= _EPS:
            return ()
        self._print_budget[trade.symbol] = budget - fill_qty
        return (PlannedFill(limit if limit is not None else trade.price, fill_qty, resting.liquidity),)

    def priority(self, resting: RestingOrder) -> tuple[float, datetime, str]:
        price = resting.limit_price if resting.limit_price is not None else resting.order.ref_price
        ordered = -price if resting.side is SignalType.BUY else price
        return ordered, resting.submitted_at, resting.broker_order_id

    def _round_lot(self, quantity: float) -> float:
        if self.lot_size is None:
            return max(0.0, float(quantity))
        return max(0.0, math.floor(float(quantity) / self.lot_size + 1e-9) * self.lot_size)

    # ----- Consumed depth ----- #

    def _consume(self, symbol: str, side: str, price: float, quantity: float) -> None:
        if symbol not in self._displayed_keys:
            book = self._books.get(symbol)
            if book is not None:
                displayed = _displayed_by_key(book)
                self._last_displayed.update(displayed)
                self._displayed_keys[symbol] = set(displayed)
        key = (symbol, side, float(price))
        self._consumed[key] = self._consumed.get(key, 0.0) + float(quantity)
        self._consumed_keys.setdefault((symbol, side), set()).add(key)

    def _reconcile(self, book: OrderBook) -> None:
        if not (self._consumed_keys.get((book.symbol, "bid")) or self._consumed_keys.get((book.symbol, "ask"))):
            for key in self._displayed_keys.pop(book.symbol, ()):
                self._last_displayed.pop(key, None)
            return

        displayed = _displayed_by_key(book)
        for key in self._displayed_keys.get(book.symbol, set()) | set(displayed):
            current = displayed.get(key, 0.0)
            previous = self._last_displayed.get(key, 0.0)
            consumed = self._consumed.get(key, 0.0)

            if current <= 0.0:
                self._last_displayed.pop(key, None)
                self._forget(key)
                continue

            if consumed > 0.0:
                if previous <= 0.0:
                    consumed = 0.0
                elif current > previous:
                    consumed = max(0.0, consumed - (current - previous))
                else:
                    consumed = min(consumed, current)
                if consumed > 0.0:
                    self._consumed[key] = consumed
                else:
                    self._forget(key)

            self._last_displayed[key] = current

        live = {key for key, quantity in displayed.items() if quantity > 0.0}
        if live:
            self._displayed_keys[book.symbol] = live
        else:
            self._displayed_keys.pop(book.symbol, None)

    def _forget(self, key: tuple[str, str, float]) -> None:
        self._consumed.pop(key, None)
        keys = self._consumed_keys.get((key[0], key[1]))
        if keys is None:
            return
        keys.discard(key)
        if not keys:
            self._consumed_keys.pop((key[0], key[1]), None)


def _reaches_limit(resting: RestingOrder, price: float) -> bool:
    if resting.limit_price is None:
        return True
    if resting.side is SignalType.BUY:
        return price <= resting.limit_price
    return price >= resting.limit_price


def _displayed_at(book: OrderBook | None, side: SignalType, price: float | None) -> float:
    if book is None or price is None:
        return 0.0
    levels = book.bids if side is SignalType.BUY else book.asks
    return sum(float(level.quantity) for level in levels if abs(float(level.price) - float(price)) <= 1e-9)


def _displayed_by_key(book: OrderBook) -> dict[tuple[str, str, float], float]:
    quantities: dict[tuple[str, str, float], float] = {}
    for side, levels in (("bid", book.bids), ("ask", book.asks)):
        for level in levels:
            key = (book.symbol, side, float(level.price))
            quantities[key] = quantities.get(key, 0.0) + float(level.quantity)
    return quantities


def _remaining(
    symbol: str,
    side: str,
    levels: Iterable[BookLevel],
    consumed: Mapping[tuple[str, str, float], float],
) -> tuple[BookLevel, ...]:
    left: dict[tuple[str, str, float], float] = {}
    out: list[BookLevel] = []
    for level in levels:
        key = (symbol, side, float(level.price))
        budget = left.setdefault(key, consumed.get(key, 0.0))
        quantity = float(level.quantity)
        if budget > 0.0:
            reduction = min(quantity, budget)
            quantity -= reduction
            left[key] = budget - reduction
        if quantity > _EPS:
            out.append(BookLevel(level.price, quantity))
    return tuple(out)
