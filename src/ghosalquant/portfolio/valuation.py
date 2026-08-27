from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from functools import lru_cache
from typing import Iterable, Mapping

from ..core.types import BookLevel, OpenOrderView, OrderBook, SignalType
from .lots import LotLadder, lot_ladders


class ValuationBasis(str, Enum):
    """
    How a position was valued.

    ``EXECUTABLE``: a fresh book, valued net of our own resting orders.
    ``INDICATIVE``: a fresh book, but our own orders may still be in the displayed depth.
    ``MARKED``: no usable book, so the last mark price was used instead.
    """
    EXECUTABLE = "executable"
    INDICATIVE = "indicative"
    MARKED = "marked"


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
    basis: ValuationBasis
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
    risk_inventory_value: float

    @property
    def has_book(self) -> bool:
        return self.basis is not ValuationBasis.MARKED

    @property
    def executable_value(self) -> float:
        """Net proceeds of selling out now, falling back to the marked value without a book."""
        return self.executable_liquidation.net_proceeds if self.has_book else self.mark_value

    @property
    def indicative_value(self) -> float:
        return self.indicative_liquidation.net_proceeds if self.has_book else self.mark_value

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

    @property
    def basis_counts(self) -> dict[ValuationBasis, int]:
        counts = {basis: 0 for basis in ValuationBasis}
        for symbol in self.symbols:
            counts[symbol.basis] += 1
        return counts

    @property
    def fully_executable(self) -> bool:
        """True when every held position was valued from a fresh book."""
        return all(symbol.basis is ValuationBasis.EXECUTABLE for symbol in self.symbols)

    @property
    def marked_symbols(self) -> tuple[str, ...]:
        return tuple(symbol.symbol for symbol in self.symbols if symbol.basis is ValuationBasis.MARKED)


class PortfolioValuationEngine:
    """
    Values a portfolio three ways: marked, liquidation into displayed depth, and a conservative
    risk equity that caps every position at what it cost.

    Marked NAV is the ordinary accounting series. Liquidation equity walks the displayed bids —
    net of our own resting orders when they are known — and charges the exit fee. Risk equity caps
    each sellable slice at its acquisition cost and assigns nothing to inventory it could not sell,
    so an unrealised gain or a thin high quote never inflates buying capacity.

    **A book is never required.** A symbol with no book, a stale book, an invalidated book, or one
    marked untradable falls back to its mark price and is reported with
    ``ValuationBasis.MARKED``; it is not valued at zero. Set ``require_book=True`` to make a
    missing book an error instead, which is the right setting only when every symbol is known to
    have one.
    """

    def __init__(
        self,
        *,
        taker_fee_bps: float = 0.0,
        stale_after_seconds: float | None = None,
        books_include_open_orders: bool = False,
        require_book: bool = False,
        init_capital: float | None = None,
        currency_decimals: int = 2,
    ) -> None:
        if taker_fee_bps < 0.0:
            raise ValueError("taker_fee_bps must be non-negative")
        if stale_after_seconds is not None and stale_after_seconds <= 0.0:
            raise ValueError("stale_after_seconds must be positive when set")
        if currency_decimals < 0:
            raise ValueError("currency_decimals must be non-negative")

        self.taker_fee_bps = float(taker_fee_bps)
        self.stale_after_seconds = stale_after_seconds
        self.books_include_open_orders = bool(books_include_open_orders)
        self.require_book = bool(require_book)
        self.currency_decimals = int(currency_decimals)

        self._books: dict[str, OrderBook] = {}
        self._invalidated: set[str] = set()
        self._tradable: dict[str, bool] = {}
        self._peak_risk_equity = float(init_capital) if init_capital is not None else None
        self._last_snapshot: PortfolioValuation | None = None

    # ----- Market state ----- #

    def observe_book(self, book: OrderBook) -> None:
        self._books[book.symbol] = book
        self._invalidated.discard(book.symbol)

    def set_tradable(self, symbol: str, tradable: bool) -> None:
        self._tradable[symbol] = bool(tradable)

    def invalidate_book(self, symbol: str) -> None:
        """Stop trusting this symbol's depth until a newer book arrives, e.g. after taking liquidity."""
        self._invalidated.add(symbol)

    def book_for(self, symbol: str) -> OrderBook | None:
        return self._books.get(symbol)

    @property
    def peak_risk_equity(self) -> float | None:
        return self._peak_risk_equity

    @property
    def last_snapshot(self) -> PortfolioValuation | None:
        return self._last_snapshot

    # ----- Valuation ----- #

    def snapshot(
        self,
        time_stamp: datetime,
        *,
        portfolio,
        open_orders: Iterable[OpenOrderView] = (),
    ) -> PortfolioValuation:
        cash = float(portfolio.cash_balance)
        realised = float(portfolio.realised_pnl)
        marks = dict(portfolio.last_prices)
        ladders = lot_ladders(portfolio.open_trades)
        orders = tuple(open_orders)
        own_bids = self._own_bid_quantities(orders)
        own_orders_known = bool(orders) or not self.books_include_open_orders

        symbols = tuple(
            self._value_symbol(
                symbol, ladders[symbol], time_stamp, marks.get(symbol), own_bids, own_orders_known
            )
            for symbol in sorted(ladders)
        )

        inventory_cost = sum(symbol.inventory_cost for symbol in symbols)
        inventory_mark_value = sum(symbol.mark_value for symbol in symbols)
        valuation = PortfolioValuation(
            time_stamp=time_stamp,
            cash=cash,
            realised_pnl=realised,
            marked_nav=cash + inventory_mark_value,
            inventory_cost=inventory_cost,
            inventory_mark_value=inventory_mark_value,
            unrealised_pnl=inventory_mark_value - inventory_cost,
            indicative_liquidation_equity=cash + sum(symbol.indicative_value for symbol in symbols),
            executable_liquidation_equity=cash + sum(symbol.executable_value for symbol in symbols),
            risk_equity=cash + sum(symbol.risk_inventory_value for symbol in symbols),
            symbols=symbols,
        )
        self._last_snapshot = valuation
        self._peak_risk_equity = (
            valuation.risk_equity
            if self._peak_risk_equity is None
            else max(self._peak_risk_equity, valuation.risk_equity)
        )
        return valuation

    def risk_equity(
        self,
        time_stamp: datetime,
        *,
        portfolio,
        open_orders: Iterable[OpenOrderView] = (),
    ) -> float:
        return self.snapshot(time_stamp, portfolio=portfolio, open_orders=open_orders).risk_equity

    def _value_symbol(
        self,
        symbol: str,
        ladder: LotLadder,
        time_stamp: datetime,
        mark_price: float | None,
        own_bids: Mapping[tuple[str, float], float],
        own_orders_known: bool,
    ) -> SymbolValuation:
        quantity = ladder.quantity
        mark_value = 0.0 if mark_price is None else quantity * float(mark_price)

        book = self._books.get(symbol)
        invalidated = symbol in self._invalidated
        tradable = self._tradable.get(symbol, True)
        age = None if book is None else (time_stamp - book.time_stamp).total_seconds()
        stale = self._is_stale(age)
        usable = book is not None and not invalidated and tradable and not stale

        if not usable and self.require_book:
            raise ValueError(
                f"{symbol} has no usable order book at {time_stamp} "
                f"(book={'present' if book else 'missing'}, stale={stale}, "
                f"invalidated={invalidated}, tradable={tradable}) and require_book is set"
            )

        empty = _empty_liquidation()
        if not usable:
            return SymbolValuation(
                symbol=symbol,
                quantity=quantity,
                inventory_cost=ladder.cost,
                mark_price=mark_price,
                mark_value=mark_value,
                unrealised_pnl=mark_value - ladder.cost,
                basis=ValuationBasis.MARKED,
                book_time_stamp=None if book is None else book.time_stamp,
                book_age_seconds=age,
                book_stale=stale,
                book_invalidated=invalidated,
                tradable=tradable,
                external_bids=(),
                own_bid_quantity_removed=0.0,
                indicative_liquidation=empty,
                executable_liquidation=empty,
                sellable_cost=0.0,
                unsold_quantity=quantity,
                risk_inventory_value=max(0.0, min(mark_value, ladder.cost)),
            )

        indicative = liquidate_into_bids(
            quantity, book.bids, taker_fee_bps=self.taker_fee_bps, currency_decimals=self.currency_decimals
        )
        if self.books_include_open_orders:
            external, removed = _external_bids(symbol, book.bids, own_bids)
            basis = ValuationBasis.EXECUTABLE if own_orders_known else ValuationBasis.INDICATIVE
        else:
            external, removed = tuple(book.bids), 0.0
            basis = ValuationBasis.EXECUTABLE

        executable = (
            indicative
            if external == tuple(book.bids)
            else liquidate_into_bids(
                quantity, external, taker_fee_bps=self.taker_fee_bps, currency_decimals=self.currency_decimals
            )
        )
        sellable_cost = ladder.cost_of(executable.sellable_quantity)

        return SymbolValuation(
            symbol=symbol,
            quantity=quantity,
            inventory_cost=ladder.cost,
            mark_price=mark_price,
            mark_value=mark_value,
            unrealised_pnl=mark_value - ladder.cost,
            basis=basis,
            book_time_stamp=book.time_stamp,
            book_age_seconds=age,
            book_stale=False,
            book_invalidated=False,
            tradable=tradable,
            external_bids=external,
            own_bid_quantity_removed=removed,
            indicative_liquidation=indicative,
            executable_liquidation=executable,
            sellable_cost=sellable_cost,
            unsold_quantity=max(0.0, quantity - executable.sellable_quantity),
            risk_inventory_value=max(0.0, min(executable.net_proceeds, sellable_cost)),
        )

    def _own_bid_quantities(self, open_orders: Iterable[OpenOrderView]) -> dict[tuple[str, float], float]:
        if not self.books_include_open_orders:
            return {}
        quantities: dict[tuple[str, float], float] = {}
        for view in open_orders:
            if view.side is not SignalType.BUY:
                continue
            price = view.limit_price if view.limit_price is not None else view.order.effective_limit_price
            key = (view.symbol, _price_key(float(price)))
            quantities[key] = quantities.get(key, 0.0) + max(0.0, float(view.remaining_quantity))
        return quantities

    def _is_stale(self, age_seconds: float | None) -> bool:
        if age_seconds is None or self.stale_after_seconds is None:
            return False
        return age_seconds > self.stale_after_seconds


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
    return liquidate_into_bids(
        quantity, qualifying, taker_fee_bps=taker_fee_bps, currency_decimals=currency_decimals
    )


@lru_cache(maxsize=65_536)
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
) -> tuple[tuple[BookLevel, ...], float]:
    quantities: dict[float, float] = {}
    for level in bids or ():
        price = _price_key(float(level.price))
        quantities[price] = quantities.get(price, 0.0) + max(0.0, float(level.quantity))

    removed = 0.0
    external: list[BookLevel] = []
    for price in sorted(quantities, reverse=True):
        displayed = quantities[price]
        own = max(0.0, float(own_bids.get((symbol, price), 0.0)))
        reduction = min(displayed, own)
        removed += reduction
        quantity = displayed - reduction
        if quantity > 1e-12:
            external.append(BookLevel(float(price), quantity))
    return tuple(external), removed


def _price_key(price: float) -> float:
    return round(float(price), 10)


def _round_currency(value: float, decimals: int) -> float:
    quantum = Decimal(1).scaleb(-decimals)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))
