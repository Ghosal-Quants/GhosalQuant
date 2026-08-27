from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import count
from typing import Iterable
import heapq
import math

from .broker_base import Broker
from ..core import metadata_keys
from ..core.types import (
    Bar,
    BookLevel,
    Fill,
    Liquidity,
    OpenOrderView,
    Order,
    OrderBook,
    PublicTrade,
    SignalType,
)

_EPS = 1e-12


@dataclass(slots=True)
class RestingOrder:
    """A working order in the simulated book."""
    broker_order_id: str
    order: Order
    limit_price: float
    remaining_quantity: float
    liquidity: Liquidity
    queue_ahead_quantity: float = 0.0
    age_events: int = 0


@dataclass(frozen=True, slots=True)
class PlannedFill:
    price: float
    quantity: float
    consume_side: str | None = None
    consume_price: float | None = None


class OrderBookSimBroker(Broker):
    """
    Queue-aware simulated broker for L2 order books.

    A taker order sweeps the displayed book immediately and never rests. A maker order rests
    behind the quantity already displayed at its price and fills only when a public print trades
    through that price and consumes the queue ahead of it, or when the book itself crosses the
    order's limit. Depth this broker consumes is withheld from the book handed to the strategy
    until the venue redisplays it, so the same liquidity cannot be taken twice.

    ``maker_rebate_bps`` is credited (a negative ``Fill.commission``) and ``taker_fee_bps`` is
    charged. Both are required: passive P&L is mostly fee economics, so the venue's schedule is
    not something to inherit from a default.
    """

    def __init__(
        self,
        *,
        maker_rebate_bps: float,
        taker_fee_bps: float,
        max_order_age_events: int | None = None,
        max_order_age_seconds: float | None = None,
        public_trade_participation: float = 1.0,
        lot_size: float | None = None,
        model_queue_position: bool = True,
    ) -> None:
        super().__init__()
        if max_order_age_events is not None and max_order_age_events <= 0:
            raise ValueError("max_order_age_events must be > 0 when set")
        if max_order_age_seconds is not None and max_order_age_seconds <= 0:
            raise ValueError("max_order_age_seconds must be > 0 when set")
        if not 0.0 < public_trade_participation <= 1.0:
            raise ValueError("public_trade_participation must be in (0, 1]")
        if lot_size is not None and lot_size <= 0.0:
            raise ValueError("lot_size must be > 0 when set")

        self.maker_rebate_bps = float(maker_rebate_bps)
        self.taker_fee_bps = float(taker_fee_bps)
        self.max_order_age_events = max_order_age_events
        self.max_order_age_seconds = max_order_age_seconds
        self.public_trade_participation = float(public_trade_participation)
        self.lot_size = lot_size
        self.model_queue_position = bool(model_queue_position)

        self._id_seq = count(1)
        self._expiry_seq = count(1)
        self._orders: dict[str, RestingOrder] = {}
        self._order_ids_by_symbol: dict[str, dict[str, None]] = {}
        self._expiry_heap: list[tuple[datetime, int, str]] = []
        self._pending_fills: list[Fill] = []
        self._last_book: dict[str, OrderBook] = {}
        self._seen_trade_ids: set[str] = set()

        self._consumed: dict[tuple[str, str, float], float] = {}
        self._consumed_keys: dict[tuple[str, str], set[tuple[str, str, float]]] = {}
        self._last_displayed: dict[tuple[str, str, float], float] = {}
        self._displayed_keys: dict[str, set[tuple[str, str, float]]] = {}

    # ----- Market data ----- #

    def on_bar(self, bar: Bar) -> None:
        return None

    def on_order_book(self, book: OrderBook) -> OrderBook:
        self._reconcile_consumption(book)
        self._last_book[book.symbol] = book
        adjusted = self._adjusted_book(book)
        for order_id, resting in self._orders_for(book.symbol):
            if self._is_expired(resting, book.time_stamp):
                self._remove(order_id)
                continue
            resting.age_events += 1
            if self._fill_against_book(resting, adjusted, book.time_stamp):
                self._remove(order_id)
            elif self.max_order_age_events is not None and resting.age_events >= self.max_order_age_events:
                self._remove(order_id)
        return self._adjusted_book(book)

    def on_public_trade(self, trade: PublicTrade) -> None:
        if trade.trade_id is not None:
            if trade.trade_id in self._seen_trade_ids:
                return
            self._seen_trade_ids.add(trade.trade_id)

        self.expire_orders(now=trade.time_stamp, symbol=trade.symbol)

        traded = max(0.0, float(trade.quantity))
        available = self._round_lot(traded * self.public_trade_participation)
        if traded <= _EPS or available <= _EPS:
            return

        eligible = [
            (order_id, resting)
            for order_id, resting in self._orders_for(trade.symbol)
            if trade.time_stamp >= resting.order.time_stamp and self._print_reaches(resting, trade)
        ]
        eligible.sort(key=lambda item: self._queue_priority(item[1]))

        for order_id, resting in eligible:
            if traded <= _EPS or available <= _EPS:
                break
            consumed = min(resting.queue_ahead_quantity, traded)
            resting.queue_ahead_quantity -= consumed
            traded -= consumed
            if resting.queue_ahead_quantity > _EPS or traded <= _EPS:
                continue
            filled = self._fill_from_print(resting, trade, available=min(traded, available))
            traded -= filled
            available -= filled
            if resting.remaining_quantity <= _EPS or (filled > 0.0 and self._cancel_remainder(resting)):
                self._remove(order_id)

    def observe_unchanged_book(self, book: OrderBook) -> OrderBook:
        """Advance the known book without re-running resting-order matching."""
        self._last_book[book.symbol] = book
        return self._adjusted_book(book)

    def adjusted_book(self, book: OrderBook) -> OrderBook:
        """The book with this broker's own consumed depth removed."""
        return self._adjusted_book(book)

    # ----- Execution ----- #

    def submit(self, order: Order) -> str:
        order_id = f"OBS-{next(self._id_seq)}"
        resting = RestingOrder(
            broker_order_id=order_id,
            order=order,
            limit_price=order.effective_limit_price,
            remaining_quantity=float(order.quantity),
            liquidity=_order_liquidity(order),
        )

        book = self._last_book.get(order.symbol)
        if resting.liquidity is Liquidity.MAKER and self.model_queue_position:
            resting.queue_ahead_quantity = _displayed_at(book, order.side, resting.limit_price)

        if book is not None and book.time_stamp >= order.time_stamp:
            if self._fill_against_book(resting, self._adjusted_book(book), book.time_stamp):
                return order_id

        if resting.liquidity is Liquidity.TAKER:
            return order_id

        self._register(order_id, resting)
        return order_id

    def get_fills(self) -> list[Fill]:
        fills = self._pending_fills
        self._pending_fills = []
        return fills

    # ----- Order lifecycle ----- #

    def expire_orders(
        self,
        *,
        now: datetime,
        symbol: str | None = None,
        side: SignalType | None = None,
    ) -> list[str]:
        """Expire resting orders past their TTL, driven by a clock rather than by market data."""
        expired: list[str] = []
        candidates = self._orders_for(symbol) if symbol is not None else list(self._orders.items())
        for order_id, resting in candidates:
            if side is not None and resting.order.side is not side:
                continue
            if self._is_expired(resting, now):
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
            resting.order.symbol
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
                submitted_at=resting.order.time_stamp,
                limit_price=resting.limit_price,
                liquidity=resting.liquidity,
                queue_ahead_quantity=resting.queue_ahead_quantity,
            )
            for resting in self._orders.values()
            if symbol is None or resting.order.symbol == symbol
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
            if side is not None and resting.order.side is not side:
                continue
            if slots is not None and _order_slot(resting.order) not in slots:
                continue
            self._remove(order_id)
            cancelled += 1
        return cancelled

    # ----- Fill models ----- #

    def _fill_against_book(self, resting: RestingOrder, book: OrderBook, time_stamp: datetime) -> bool:
        planned = self._plan(resting, book)
        if not planned:
            return False

        for fill in planned:
            self._record_fill(resting, price=fill.price, quantity=fill.quantity, time_stamp=time_stamp)
            if fill.consume_side is not None and fill.consume_price is not None:
                self._consume(resting.order.symbol, fill.consume_side, fill.consume_price, fill.quantity)
            resting.remaining_quantity -= fill.quantity

        if resting.liquidity is Liquidity.TAKER:
            resting.remaining_quantity = 0.0
            return True
        return resting.remaining_quantity <= _EPS or self._cancel_remainder(resting)

    def _plan(self, resting: RestingOrder, book: OrderBook) -> list[PlannedFill]:
        quantity = self._round_lot(resting.remaining_quantity)
        if quantity <= _EPS:
            return []

        buying = resting.order.side is SignalType.BUY
        levels = book.asks if buying else book.bids
        side = "ask" if buying else "bid"
        taking = resting.liquidity is Liquidity.TAKER

        planned: list[PlannedFill] = []
        remaining = quantity
        for level in levels:
            if remaining <= _EPS:
                break
            reached = level.price <= resting.limit_price if buying else level.price >= resting.limit_price
            if not reached:
                break
            fill_qty = self._round_lot(min(remaining, float(level.quantity)))
            if fill_qty <= _EPS:
                break
            # A taker pays the level; a resting maker that the book crossed keeps its own price.
            planned.append(PlannedFill(level.price if taking else resting.limit_price, fill_qty, side, level.price))
            remaining -= fill_qty
        return planned

    def _fill_from_print(self, resting: RestingOrder, trade: PublicTrade, *, available: float) -> float:
        fill_qty = self._round_lot(min(resting.remaining_quantity, available))
        if fill_qty <= _EPS:
            return 0.0
        self._record_fill(
            resting,
            price=resting.limit_price,
            quantity=fill_qty,
            time_stamp=trade.effective_observed_at,
        )
        resting.remaining_quantity -= fill_qty
        return fill_qty

    def _record_fill(self, resting: RestingOrder, *, price: float, quantity: float, time_stamp: datetime) -> None:
        self._pending_fills.append(
            Fill(
                time_stamp=time_stamp,
                symbol=resting.order.symbol,
                side=resting.order.side,
                quantity=quantity,
                price=price,
                broker_order_id=resting.broker_order_id,
                commission=self._commission(resting.liquidity, price * quantity),
                liquidity=resting.liquidity,
            )
        )

    def _commission(self, liquidity: Liquidity, notional: float) -> float:
        if liquidity is Liquidity.TAKER:
            return notional * (self.taker_fee_bps / 10_000.0)
        return -notional * (self.maker_rebate_bps / 10_000.0)

    def _round_lot(self, quantity: float) -> float:
        if self.lot_size is None:
            return max(0.0, float(quantity))
        lots = math.floor(float(quantity) / self.lot_size + 1e-9)
        return max(0.0, lots * self.lot_size)

    @staticmethod
    def _print_reaches(resting: RestingOrder, trade: PublicTrade) -> bool:
        if resting.liquidity is Liquidity.TAKER:
            return False
        if resting.order.side is SignalType.BUY:
            return trade.price <= resting.limit_price
        return trade.price >= resting.limit_price

    @staticmethod
    def _queue_priority(resting: RestingOrder) -> tuple[float, datetime, str]:
        price = -resting.limit_price if resting.order.side is SignalType.BUY else resting.limit_price
        return price, resting.order.time_stamp, resting.broker_order_id

    @staticmethod
    def _cancel_remainder(resting: RestingOrder) -> bool:
        metadata = resting.order.metadata or {}
        return bool(metadata.get(metadata_keys.CANCEL_REMAINDER_AFTER_FIRST_FILL))

    # ----- Consumed depth ----- #

    def _consume(self, symbol: str, side: str, price: float, quantity: float) -> None:
        if symbol not in self._displayed_keys:
            book = self._last_book.get(symbol)
            if book is not None:
                displayed = _displayed_by_key(book)
                self._last_displayed.update(displayed)
                self._displayed_keys[symbol] = set(displayed)
        key = (symbol, side, float(price))
        self._consumed[key] = self._consumed.get(key, 0.0) + float(quantity)
        self._consumed_keys.setdefault((symbol, side), set()).add(key)

    def _adjusted_book(self, book: OrderBook) -> OrderBook:
        bid_keys = self._consumed_keys.get((book.symbol, "bid"))
        ask_keys = self._consumed_keys.get((book.symbol, "ask"))
        if not bid_keys and not ask_keys:
            return book
        return book.replace(
            bids=_remaining_levels(book.symbol, "bid", book.bids, self._consumed) if bid_keys else book.bids,
            asks=_remaining_levels(book.symbol, "ask", book.asks, self._consumed) if ask_keys else book.asks,
        )

    def _reconcile_consumption(self, book: OrderBook) -> None:
        """Forget consumed depth once the venue has redisplayed or withdrawn it."""
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
                self._forget_consumption(key)
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
                    self._forget_consumption(key)

            self._last_displayed[key] = current

        live = {key for key, quantity in displayed.items() if quantity > 0.0}
        if live:
            self._displayed_keys[book.symbol] = live
        else:
            self._displayed_keys.pop(book.symbol, None)

    def _forget_consumption(self, key: tuple[str, str, float]) -> None:
        self._consumed.pop(key, None)
        keys = self._consumed_keys.get((key[0], key[1]))
        if keys is None:
            return
        keys.discard(key)
        if not keys:
            self._consumed_keys.pop((key[0], key[1]), None)

    # ----- Book-keeping ----- #

    def _register(self, order_id: str, resting: RestingOrder) -> None:
        self._orders[order_id] = resting
        self._order_ids_by_symbol.setdefault(resting.order.symbol, {})[order_id] = None
        expires_at = self._expires_at(resting)
        if expires_at is not None:
            heapq.heappush(self._expiry_heap, (expires_at, next(self._expiry_seq), order_id))

    def _remove(self, order_id: str) -> RestingOrder | None:
        resting = self._orders.pop(order_id, None)
        if resting is None:
            return None
        by_symbol = self._order_ids_by_symbol.get(resting.order.symbol)
        if by_symbol is not None:
            by_symbol.pop(order_id, None)
            if not by_symbol:
                self._order_ids_by_symbol.pop(resting.order.symbol, None)
        return resting

    def _orders_for(self, symbol: str) -> list[tuple[str, RestingOrder]]:
        return [
            (order_id, self._orders[order_id])
            for order_id in self._order_ids_by_symbol.get(symbol, ())
            if order_id in self._orders
        ]

    def _max_age_seconds(self, order: Order) -> float | None:
        metadata = order.metadata or {}
        value = metadata.get(metadata_keys.MAX_ORDER_AGE_SECONDS)
        if value is not None:
            return float(value) if float(value) > 0.0 else None
        return self.max_order_age_seconds

    def _expires_at(self, resting: RestingOrder) -> datetime | None:
        max_age = self._max_age_seconds(resting.order)
        if max_age is None:
            return None
        return resting.order.time_stamp + timedelta(seconds=max_age)

    def _is_expired(self, resting: RestingOrder, now: datetime) -> bool:
        expires_at = self._expires_at(resting)
        return expires_at is not None and now >= expires_at


def _order_slot(order: Order) -> str | None:
    metadata = order.metadata or {}
    slot = metadata.get(metadata_keys.ORDER_SLOT)
    return None if slot is None else str(slot)


def _order_liquidity(order: Order) -> Liquidity:
    if order.liquidity is not None:
        return order.liquidity
    metadata = order.metadata or {}
    value = metadata.get(metadata_keys.LIQUIDITY)
    return Liquidity(str(value).lower()) if value is not None else Liquidity.MAKER


def _displayed_at(book: OrderBook | None, side: SignalType, price: float) -> float:
    if book is None:
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


def _remaining_levels(
    symbol: str,
    side: str,
    levels: tuple[BookLevel, ...],
    consumed: dict[tuple[str, str, float], float],
) -> tuple[BookLevel, ...]:
    left: dict[tuple[str, str, float], float] = {}
    remaining: list[BookLevel] = []
    for level in levels:
        key = (symbol, side, float(level.price))
        budget = left.setdefault(key, consumed.get(key, 0.0))
        quantity = float(level.quantity)
        if budget > 0.0:
            reduction = min(quantity, budget)
            quantity -= reduction
            left[key] = budget - reduction
        if quantity > _EPS:
            remaining.append(BookLevel(level.price, quantity))
    return tuple(remaining)
