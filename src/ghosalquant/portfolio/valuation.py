from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
import math
from typing import Iterable, Mapping

from ..core.types import BookLevel, OrderBook, SignalType
from .lots import LotLadder, lot_ladders


@dataclass(frozen=True, slots=True)
class DepthLiquidation:
    sellable_quantity: float
    gross_proceeds: float
    exit_fee: float
    net_proceeds: float
    vwap: float | None
    levels_used: int
    worst_price: float | None


@dataclass(frozen=True, slots=True)
class SymbolValuation:
    symbol: str
    quantity: float
    inventory_cost: float
    mark_price: float | None
    mark_value: float
    unrealised_pnl: float
    book_time_stamp: datetime | None
    book_age_seconds: float | None
    book_stale: bool
    book_invalidated: bool
    tradable: bool
    external_bids: tuple[BookLevel, ...]
    own_bid_quantity_removed: float
    indicative_liquidation: DepthLiquidation
    executable_liquidation: DepthLiquidation
    sellable_cost: float
    unsold_quantity: float
    unsold_cost: float
    risk_inventory_value: float

    @property
    def executable_pnl_after_exit_fee(self) -> float:
        return self.executable_liquidation.net_proceeds - self.sellable_cost


@dataclass(frozen=True, slots=True)
class PortfolioValuation:
    time_stamp: datetime
    cash: float
    realised_pnl: float
    marked_nav: float
    inventory_cost: float
    inventory_mark_value: float
    unrealised_pnl: float
    indicative_liquidation_equity: float
    executable_liquidation_equity: float
    risk_equity: float
    symbols: tuple[SymbolValuation, ...]


@dataclass(frozen=True, slots=True)
class _RiskValue:
    external_bids: tuple[BookLevel, ...]
    own_bid_quantity_removed: float
    liquidation: DepthLiquidation
    sellable_cost: float
    risk_inventory_value: float


class PortfolioValuationEngine:
    """
    Values a long portfolio three ways: marked, liquidation into displayed depth, and a conservative
    risk equity that caps each position at what it cost.

    Marked NAV is the portfolio's own accounting series. Liquidation equity walks the fresh bids of
    each held symbol, net of our own resting bids when ``books_include_open_orders``, and charges the
    exit fee. Risk equity counts, per position, ``min(net proceeds, acquisition cost of the sellable
    slice)``, so an unrealised gain or a thin high quote never inflates buying capacity. A position
    whose book is missing, stale, invalidated or untradable counts at its mark capped at cost when
    ``value_unbooked_at_mark``, and as nothing otherwise.

    A venue's raw book and the book left after our own simulated consumption can differ: staleness
    is judged on the raw book, depth on the executable one. Valuations are cached per symbol, so a
    caller can ask for risk equity on every event.
    """

    def __init__(
        self,
        *,
        init_capital: float | None = None,
        taker_fee_bps: float = 0.0,
        stale_after_seconds: float | None = None,
        books_include_open_orders: bool = False,
        value_unbooked_at_mark: bool = True,
        lot_size: float | None = None,
        currency_decimals: int = 2,
    ) -> None:
        if init_capital is not None and init_capital <= 0.0:
            raise ValueError("init_capital must be positive when set")
        if taker_fee_bps < 0.0:
            raise ValueError("taker_fee_bps must be non-negative")
        if stale_after_seconds is not None and stale_after_seconds <= 0.0:
            raise ValueError("stale_after_seconds must be positive when set")
        if lot_size is not None and lot_size <= 0.0:
            raise ValueError("lot_size must be > 0 when set")
        if currency_decimals < 0:
            raise ValueError("currency_decimals must be non-negative")
        self.taker_fee_bps = float(taker_fee_bps)
        self.stale_after_seconds = None if stale_after_seconds is None else float(stale_after_seconds)
        self.books_include_open_orders = bool(books_include_open_orders)
        self.value_unbooked_at_mark = bool(value_unbooked_at_mark)
        self.lot_size = lot_size
        self.currency_decimals = int(currency_decimals)
        self._raw_books: dict[str, OrderBook] = {}
        self._executable_books: dict[str, OrderBook] = {}
        self._invalidated_at: dict[str, datetime] = {}
        self._tradable: dict[str, bool] = {}
        self._peak_risk_equity = None if init_capital is None else float(init_capital)
        self._last_snapshot: PortfolioValuation | None = None
        self._last_snapshot_key: tuple | None = None
        self._accounting_version: int | None = None
        self._ladders: dict[str, LotLadder] = {}
        self._position_symbols: tuple[str, ...] = ()
        self._market_version = 0
        self._symbol_market_version: dict[str, int] = {}
        self._depth_cache: dict[tuple, tuple[tuple[BookLevel, ...], float]] = {}
        self._value_cache: dict[str, tuple[tuple, SymbolValuation]] = {}
        self._risk_cache: dict[str, tuple[tuple, _RiskValue | None]] = {}
        self._risk_dirty: set[str] = set()
        self._risk_deadline: dict[str, datetime] = {}
        self._next_risk_deadline: datetime | None = None
        self._risk_own_signatures: dict[str, tuple[tuple[float, float], ...]] = {}
        self._risk_cash: float | None = None
        self._last_risk_equity: float | None = None
        self._held_market_version = 0
        self._stale_version = 0
        self._stale_deadline: dict[str, datetime] = {}
        self._next_stale_deadline: datetime | None = None

    # ----- Market state ----- #

    @property
    def peak_risk_equity(self) -> float | None:
        return self._peak_risk_equity

    def restart_peak_risk_equity(self, equity: float) -> None:
        """Restart the drawdown peak at ``equity``; later valuations raise it."""
        equity = float(equity)
        if not math.isfinite(equity) or equity <= 0.0:
            raise ValueError("peak risk equity must be positive and finite")
        self._peak_risk_equity = equity

    @property
    def last_snapshot(self) -> PortfolioValuation | None:
        return self._last_snapshot

    @property
    def invalidated_at_by_symbol(self) -> Mapping[str, datetime]:
        return dict(self._invalidated_at)

    def book_for(self, symbol: str) -> OrderBook | None:
        return self._raw_books.get(symbol)

    def observe_book(self, book: OrderBook, executable_book: OrderBook | None = None, *, tradable: bool | None = None) -> None:
        """A new venue book, and the book left after our own simulated consumption when that differs."""
        symbol = book.symbol
        self._raw_books[symbol] = book
        self._executable_books[symbol] = executable_book or book
        if tradable is not None:
            self._tradable[symbol] = bool(tradable)
        invalidated_at = self._invalidated_at.get(symbol)
        if invalidated_at is not None and book.time_stamp >= invalidated_at:
            self._invalidated_at.pop(symbol, None)
        self._touch(symbol)

    def observe_executable_book(self, book: OrderBook) -> None:
        """New depth after our own consumption, without making the venue's book any newer."""
        previous = self._executable_books.get(book.symbol)
        self._executable_books[book.symbol] = book
        if previous is None or previous.bids != book.bids:
            self._touch(book.symbol)

    def set_tradable(self, symbol: str, tradable: bool) -> None:
        if self._tradable.get(symbol) != bool(tradable):
            self._tradable[symbol] = bool(tradable)
            self._touch(symbol)

    def invalidate_book(self, symbol: str, at: datetime) -> None:
        """Stop trusting the symbol's book from ``at`` until a book from then or later arrives."""
        previous = self._invalidated_at.get(symbol)
        if previous is not None and at <= previous:
            return
        seen = self._book_time(symbol)
        was_invalidated = previous is not None and (seen is None or previous >= seen)
        self._invalidated_at[symbol] = at
        if (seen is None or at >= seen) != was_invalidated:
            self._touch(symbol)

    def observe_trade(self, trade) -> None:
        """A print invalidates a book that predates it: the depth it traded against may be gone."""
        book = self._raw_books.get(trade.symbol)
        if book is None or trade.time_stamp >= book.time_stamp:
            self.invalidate_book(trade.symbol, trade.time_stamp)

    # ----- Valuation ----- #

    def position_symbols(self, portfolio) -> tuple[str, ...]:
        self._refresh_positions(portfolio)
        return self._position_symbols

    def change_token(self, time_stamp: datetime, *, portfolio) -> tuple:
        """Equal tokens mean an unchanged valuation: positions, cash, the books of held symbols and their staleness."""
        accounting_version = self._refresh_positions(portfolio)
        if self._next_stale_deadline is not None and time_stamp > self._next_stale_deadline:
            expired = [symbol for symbol, deadline in self._stale_deadline.items() if time_stamp > deadline]
            if expired:
                self._stale_version += 1
                for symbol in expired:
                    self._stale_deadline.pop(symbol, None)
                self._next_stale_deadline = min(self._stale_deadline.values(), default=None)
        return (
            accounting_version,
            float(portfolio.cash_balance),
            float(portfolio.realised_pnl),
            float(portfolio.current_equity),
            self._held_market_version,
            self._stale_version,
        )

    def snapshot(
        self,
        time_stamp: datetime,
        *,
        portfolio,
        open_orders: Iterable[object] = (),
        refresh_book_age: bool = True,
    ) -> PortfolioValuation:
        """Value the portfolio and raise the peak risk equity. With ``refresh_book_age`` false a cached symbol keeps
        the book age it was valued with."""
        orders = tuple(open_orders) if self.books_include_open_orders else ()
        accounting_version = self._refresh_positions(portfolio)
        marks = portfolio.last_prices
        key = (
            time_stamp,
            accounting_version,
            self._market_version,
            tuple(sorted((str(symbol), float(price)) for symbol, price in marks.items())),
            _order_signature(orders) if self.books_include_open_orders else (),
            bool(refresh_book_age),
        )
        if key == self._last_snapshot_key and self._last_snapshot is not None:
            return self._last_snapshot
        valuation = self._value_incremental(time_stamp, portfolio, orders, marks, refresh_book_age)
        self._last_snapshot, self._last_snapshot_key = valuation, key
        self._raise_peak(valuation.risk_equity)
        return valuation

    def observational_snapshot(self, time_stamp: datetime, *, portfolio, open_orders: Iterable[object] = ()) -> PortfolioValuation:
        """Value the portfolio without touching the engine's state, so an audit cannot change later risk."""
        return value_portfolio(
            time_stamp,
            portfolio=portfolio,
            executable_books=dict(self._executable_books),
            raw_books=dict(self._raw_books),
            open_orders=tuple(open_orders),
            tradable=dict(self._tradable),
            invalidated_at=dict(self._invalidated_at),
            books_include_open_orders=self.books_include_open_orders,
            value_unbooked_at_mark=self.value_unbooked_at_mark,
            taker_fee_bps=self.taker_fee_bps,
            stale_after_seconds=self.stale_after_seconds,
            lot_size=self.lot_size,
            currency_decimals=self.currency_decimals,
            marks=dict(portfolio.last_prices),
        )

    def risk_equity(self, time_stamp: datetime, *, portfolio, open_orders: Iterable[object] = ()) -> float:
        """Risk equity alone, revaluing only the symbols whose book, own orders, staleness or position changed."""
        orders = tuple(open_orders) if self.books_include_open_orders else ()
        accounting_version = self._refresh_positions(portfolio)
        own_bids, own_signatures = self._own_bids(orders)
        if own_signatures != self._risk_own_signatures:
            self._risk_dirty.update(
                symbol
                for symbol in set(own_signatures) | set(self._risk_own_signatures)
                if own_signatures.get(symbol, ()) != self._risk_own_signatures.get(symbol, ())
            )
            self._risk_own_signatures = own_signatures
        if self._next_risk_deadline is not None and time_stamp > self._next_risk_deadline:
            self._risk_dirty.update(symbol for symbol, deadline in self._risk_deadline.items() if time_stamp > deadline)

        changed = False
        for symbol in self._position_symbols:
            if symbol not in self._risk_dirty and symbol in self._risk_cache:
                continue
            seen = self._book_time(symbol)
            stale = self._is_stale(symbol, time_stamp, seen)
            own_signature = own_signatures.get(symbol, ())
            key = (accounting_version, self._symbol_market_version.get(symbol, 0), stale, own_signature)
            value = None if stale else self._risk_value(symbol, self._ladders[symbol], own_bids, own_signature)
            self._risk_cache[symbol] = (key, value)
            if not stale and seen is not None and self.stale_after_seconds is not None:
                self._risk_deadline[symbol] = seen + timedelta(seconds=self.stale_after_seconds)
            else:
                self._risk_deadline.pop(symbol, None)
            changed = True
        self._risk_dirty.clear()
        self._next_risk_deadline = min(self._risk_deadline.values(), default=None)

        cash = float(portfolio.cash_balance)
        if changed or self._last_risk_equity is None or cash != self._risk_cash:
            inventory = 0.0
            for symbol in self._position_symbols:
                value = self._risk_cache[symbol][1]
                inventory += self._unbooked_risk_value(symbol, portfolio) if value is None else value.risk_inventory_value
            equity = cash + inventory
            self._last_risk_equity, self._risk_cash = equity, cash
        else:
            equity = self._last_risk_equity
        self._raise_peak(equity)
        return equity

    # ----- Internals ----- #

    def _raise_peak(self, equity: float) -> None:
        self._peak_risk_equity = equity if self._peak_risk_equity is None else max(self._peak_risk_equity, equity)

    def _refresh_positions(self, portfolio) -> int | None:
        accounting_version = getattr(portfolio, "accounting_version", None)
        if accounting_version is None or accounting_version != self._accounting_version:
            self._ladders = lot_ladders(portfolio.open_trades)
            self._position_symbols = tuple(sorted(self._ladders))
            self._accounting_version = accounting_version
            self._value_cache.clear()
            self._risk_cache.clear()
            self._risk_deadline.clear()
            self._next_risk_deadline = None
            self._risk_dirty.update(self._ladders)
            self._risk_cash = None
            self._last_risk_equity = None
            self._stale_deadline.clear()
            self._next_stale_deadline = None
        return accounting_version

    def _touch(self, symbol: str) -> None:
        self._market_version += 1
        self._symbol_market_version[symbol] = self._symbol_market_version.get(symbol, 0) + 1
        if symbol in self._ladders:
            self._held_market_version += 1
        self._value_cache.pop(symbol, None)
        self._risk_cache.pop(symbol, None)
        self._risk_deadline.pop(symbol, None)
        self._stale_deadline.pop(symbol, None)
        self._risk_dirty.add(symbol)
        self._next_risk_deadline = min(self._risk_deadline.values(), default=None)
        self._next_stale_deadline = min(self._stale_deadline.values(), default=None)

    def _own_bids(self, orders: tuple[object, ...]) -> tuple[dict[tuple[str, float], float], dict[str, tuple]]:
        own: dict[tuple[str, float], float] = {}
        for order in orders:
            if getattr(order, "side", None) is not SignalType.BUY:
                continue
            key = (str(order.symbol), _price_key(float(order.limit_price)))
            own[key] = own.get(key, 0.0) + max(0.0, float(order.remaining_quantity))
        by_symbol: dict[str, list[tuple[float, float]]] = {}
        for (symbol, price), quantity in own.items():
            by_symbol.setdefault(symbol, []).append((price, quantity))
        return own, {symbol: tuple(sorted(values)) for symbol, values in by_symbol.items()}

    def _book_time(self, symbol: str) -> datetime | None:
        book = self._raw_books.get(symbol)
        return None if book is None else book.time_stamp

    def _is_stale(self, symbol: str, now: datetime, seen: datetime | None) -> bool:
        invalidated_at = self._invalidated_at.get(symbol)
        return (
            seen is None
            or (self.stale_after_seconds is not None and (now - seen).total_seconds() > self.stale_after_seconds)
            or (invalidated_at is not None and invalidated_at >= seen)
            or not self._tradable.get(symbol, True)
        )

    def _external_depth(self, symbol: str, own_bids: Mapping, own_signature: tuple) -> tuple[tuple[BookLevel, ...], float]:
        book = self._executable_books.get(symbol)
        bids = () if book is None else book.bids
        key = (symbol, bids, own_signature)
        depth = self._depth_cache.get(key)
        if depth is None:
            depth = self._depth_cache[key] = _external_bids(symbol, bids, own_bids, self.lot_size)
        return depth

    def _risk_value(self, symbol: str, ladder: LotLadder, own_bids: Mapping, own_signature: tuple) -> _RiskValue:
        external, removed = self._external_depth(symbol, own_bids, own_signature)
        liquidation = liquidate_into_bids(
            ladder.quantity, external, taker_fee_bps=self.taker_fee_bps, currency_decimals=self.currency_decimals
        )
        sellable_cost = ladder.cost_of(liquidation.sellable_quantity)
        return _RiskValue(external, removed, liquidation, sellable_cost, max(0.0, min(liquidation.net_proceeds, sellable_cost)))

    def _unbooked_risk_value(self, symbol: str, portfolio) -> float:
        if not self.value_unbooked_at_mark:
            return 0.0
        ladder = self._ladders[symbol]
        mark = portfolio.last_prices.get(symbol)
        return 0.0 if mark is None else max(0.0, min(ladder.quantity * float(mark), ladder.cost))

    def _value_incremental(
        self,
        now: datetime,
        portfolio,
        orders: tuple[object, ...],
        marks: Mapping[str, float],
        refresh_book_age: bool,
    ) -> PortfolioValuation:
        own_bids, own_signatures = self._own_bids(orders)
        values: list[SymbolValuation] = []
        for symbol in self._position_symbols:
            ladder = self._ladders[symbol]
            seen = self._book_time(symbol)
            book_age = None if seen is None else max(0.0, (now - seen).total_seconds())
            stale = self._is_stale(symbol, now, seen)
            if not stale and seen is not None and self.stale_after_seconds is not None:
                self._stale_deadline[symbol] = seen + timedelta(seconds=self.stale_after_seconds)
            else:
                self._stale_deadline.pop(symbol, None)
            mark = marks.get(symbol)
            mark_price = None if mark is None else float(mark)
            own_signature = own_signatures.get(symbol, ())
            key = (self._accounting_version, self._symbol_market_version.get(symbol, 0), mark_price, stale, own_signature)
            cached = self._value_cache.get(symbol)
            if cached is not None and cached[0] == key:
                value = replace(cached[1], book_age_seconds=book_age) if refresh_book_age else cached[1]
            else:
                risk_key = (self._accounting_version, self._symbol_market_version.get(symbol, 0), stale, own_signature)
                cached_risk = self._risk_cache.get(symbol)
                risk = cached_risk[1] if cached_risk is not None and cached_risk[0] == risk_key else None
                if risk is None:
                    risk = self._risk_value(symbol, ladder, own_bids, own_signature)
                    if not stale:
                        self._risk_cache[symbol] = (risk_key, risk)
                value = _symbol_value(
                    symbol, ladder, mark_price, seen, book_age, stale,
                    self._invalidated_at.get(symbol), self._tradable.get(symbol, True), risk, self.value_unbooked_at_mark,
                )
                self._value_cache[symbol] = (key, value)
            values.append(value)
        self._next_stale_deadline = min(self._stale_deadline.values(), default=None)
        return _portfolio_value(now, portfolio, values)


def value_portfolio(
    time_stamp: datetime,
    *,
    portfolio,
    executable_books: Mapping[str, OrderBook],
    raw_books: Mapping[str, OrderBook],
    open_orders: Iterable[object],
    tradable: Mapping[str, bool] | None,
    invalidated_at: Mapping[str, datetime] | None,
    books_include_open_orders: bool,
    taker_fee_bps: float = 0.0,
    stale_after_seconds: float | None = None,
    value_unbooked_at_mark: bool = True,
    lot_size: float | None = None,
    currency_decimals: int = 2,
    marks: Mapping[str, float] | None = None,
    ladders: Mapping[str, LotLadder] | None = None,
) -> PortfolioValuation:
    """The engine's valuation as a pure function of the books and positions given."""
    ladders = lot_ladders(portfolio.open_trades) if ladders is None else ladders
    marks = portfolio.last_prices if marks is None else marks
    own: dict[tuple[str, float], float] = {}
    if books_include_open_orders:
        for order in open_orders:
            if getattr(order, "side", None) is not SignalType.BUY:
                continue
            key = (str(order.symbol), _price_key(float(order.limit_price)))
            own[key] = own.get(key, 0.0) + max(0.0, float(order.remaining_quantity))

    values: list[SymbolValuation] = []
    for symbol in sorted(ladders):
        ladder = ladders[symbol]
        book = executable_books.get(symbol)
        raw = raw_books.get(symbol)
        external, removed = _external_bids(symbol, () if book is None else book.bids, own, lot_size)
        liquidation = liquidate_into_bids(
            ladder.quantity, external, taker_fee_bps=taker_fee_bps, currency_decimals=currency_decimals
        )
        seen = None if raw is None else raw.time_stamp
        book_age = None if seen is None else max(0.0, (time_stamp - seen).total_seconds())
        invalidated = (invalidated_at or {}).get(symbol)
        is_tradable = bool((tradable or {}).get(symbol, True))
        stale = (
            seen is None
            or (stale_after_seconds is not None and book_age > stale_after_seconds)
            or (invalidated is not None and invalidated >= seen)
            or not is_tradable
        )
        sellable_cost = ladder.cost_of(liquidation.sellable_quantity)
        risk = _RiskValue(external, removed, liquidation, sellable_cost, max(0.0, min(liquidation.net_proceeds, sellable_cost)))
        mark = marks.get(symbol)
        values.append(_symbol_value(
            symbol, ladder, None if mark is None else float(mark), seen, book_age, stale, invalidated, is_tradable, risk,
            value_unbooked_at_mark,
        ))
    return _portfolio_value(time_stamp, portfolio, values)


def liquidate_into_bids(
    quantity: float,
    bids: Iterable[BookLevel],
    *,
    taker_fee_bps: float = 0.0,
    currency_decimals: int = 2,
) -> DepthLiquidation:
    """Walk each displayed bid level once and value only the quantity that actually fills."""
    return _liquidate_cached(float(quantity), tuple(bids or ()), float(taker_fee_bps), int(currency_decimals))


def profitable_bid_liquidation(
    quantity: float,
    bids: Iterable[BookLevel],
    *,
    minimum_net_unit_proceeds: float,
    taker_fee_bps: float = 0.0,
    currency_decimals: int = 2,
) -> DepthLiquidation:
    """Liquidate only into the contiguous depth whose fee-net unit price clears a floor."""
    fee_factor = 1.0 - float(taker_fee_bps) / 10_000.0
    qualifying: list[BookLevel] = []
    for level in bids or ():
        if float(level.price) * fee_factor + 1e-12 < minimum_net_unit_proceeds:
            break
        qualifying.append(level)
    return liquidate_into_bids(quantity, qualifying, taker_fee_bps=taker_fee_bps, currency_decimals=currency_decimals)


def _symbol_value(
    symbol: str,
    ladder: LotLadder,
    mark_price: float | None,
    seen: datetime | None,
    book_age: float | None,
    stale: bool,
    invalidated_at: datetime | None,
    tradable: bool,
    risk: _RiskValue,
    value_unbooked_at_mark: bool,
) -> SymbolValuation:
    executable = _empty_liquidation() if stale else risk.liquidation
    sellable_cost = ladder.cost_of(executable.sellable_quantity)
    mark_value = 0.0 if mark_price is None else ladder.quantity * mark_price
    if not stale:
        risk_inventory_value = max(0.0, min(executable.net_proceeds, sellable_cost))
    elif value_unbooked_at_mark:
        risk_inventory_value = max(0.0, min(mark_value, ladder.cost))
    else:
        risk_inventory_value = 0.0
    return SymbolValuation(
        symbol=symbol,
        quantity=ladder.quantity,
        inventory_cost=ladder.cost,
        mark_price=mark_price,
        mark_value=mark_value,
        unrealised_pnl=mark_value - ladder.cost,
        book_time_stamp=seen,
        book_age_seconds=book_age,
        book_stale=stale,
        book_invalidated=invalidated_at is not None and (seen is None or invalidated_at >= seen),
        tradable=tradable,
        external_bids=risk.external_bids,
        own_bid_quantity_removed=risk.own_bid_quantity_removed,
        indicative_liquidation=risk.liquidation,
        executable_liquidation=executable,
        sellable_cost=sellable_cost,
        unsold_quantity=max(0.0, ladder.quantity - executable.sellable_quantity),
        unsold_cost=max(0.0, ladder.cost - sellable_cost),
        risk_inventory_value=risk_inventory_value,
    )


def _portfolio_value(time_stamp: datetime, portfolio, values: list[SymbolValuation]) -> PortfolioValuation:
    cash = float(portfolio.cash_balance)
    inventory_cost = mark_value = unrealised = indicative = executable = risk = 0.0
    for value in values:
        inventory_cost += value.inventory_cost
        mark_value += value.mark_value
        unrealised += value.unrealised_pnl
        indicative += value.indicative_liquidation.net_proceeds
        executable += value.executable_liquidation.net_proceeds
        risk += value.risk_inventory_value
    return PortfolioValuation(
        time_stamp=time_stamp,
        cash=cash,
        realised_pnl=float(portfolio.realised_pnl),
        marked_nav=float(portfolio.current_equity),
        inventory_cost=inventory_cost,
        inventory_mark_value=mark_value,
        unrealised_pnl=unrealised,
        indicative_liquidation_equity=cash + indicative,
        executable_liquidation_equity=cash + executable,
        risk_equity=cash + risk,
        symbols=tuple(values),
    )


@lru_cache(maxsize=131_072)
def _liquidate_cached(
    quantity: float,
    bids: tuple[BookLevel, ...],
    taker_fee_bps: float,
    currency_decimals: int,
) -> DepthLiquidation:
    remaining = max(0.0, quantity)
    gross = 0.0
    sold = 0.0
    levels_used = 0
    worst_price: float | None = None
    for level in bids:
        available = max(0.0, float(level.quantity))
        if available <= 0.0:
            continue
        take = min(remaining, available)
        if take <= 0.0:
            break
        gross += take * float(level.price)
        sold += take
        remaining -= take
        levels_used += 1
        worst_price = float(level.price)
        if remaining <= 1e-12:
            break
    gross = _round_currency(gross, currency_decimals)
    fee = _round_currency(gross * taker_fee_bps / 10_000.0, currency_decimals)
    return DepthLiquidation(
        sellable_quantity=sold,
        gross_proceeds=gross,
        exit_fee=fee,
        net_proceeds=_round_currency(gross - fee, currency_decimals),
        vwap=None if sold <= 0.0 else gross / sold,
        levels_used=levels_used,
        worst_price=worst_price,
    )


def _empty_liquidation() -> DepthLiquidation:
    return DepthLiquidation(0.0, 0.0, 0.0, 0.0, None, 0, None)


def _external_bids(
    symbol: str,
    bids: Iterable[BookLevel],
    own_bids: Mapping[tuple[str, float], float],
    lot_size: float | None,
) -> tuple[tuple[BookLevel, ...], float]:
    """The bid levels by price, less our own bids at each price, in whole lots when ``lot_size`` is set."""
    quantities: dict[float, float] = {}
    for level in bids or ():
        price = _price_key(float(level.price))
        quantities[price] = quantities.get(price, 0.0) + max(0.0, float(level.quantity))

    removed = 0.0
    external: list[BookLevel] = []
    for price in sorted(quantities, reverse=True):
        displayed = quantities[price]
        reduction = min(displayed, max(0.0, float(own_bids.get((symbol, price), 0.0))))
        removed += reduction
        quantity = max(0.0, displayed - reduction)
        if lot_size is not None:
            quantity = math.floor(quantity / lot_size + 1e-12) * lot_size
        if quantity > 1e-12:
            external.append(BookLevel(float(price), quantity))
    return tuple(external), removed


def _order_signature(orders: Iterable[object]) -> tuple:
    return tuple(sorted(
        (str(order.symbol), str(order.side.value), float(order.remaining_quantity), float(order.limit_price))
        for order in orders
    ))


def _price_key(price: float) -> float:
    return round(float(price), 8)


@lru_cache(maxsize=131_072)
def _round_currency(value: float, decimals: int) -> float:
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))
