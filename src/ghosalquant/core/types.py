from dataclasses import dataclass, asdict, field, replace as _dataclass_replace
from datetime import datetime
from enum import Enum
from typing import Any, Union
import warnings


@dataclass(frozen=True, slots=True)
class Bar:
    time_stamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float

class SignalType(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'


class Liquidity(str, Enum):
    """
    Whether an order is expected to add liquidity (rest as a maker) or take it (cross the spread).

    Brokers report the realised liquidity on the ``Fill``; strategies may declare the intended
    liquidity on a ``Signal`` so fee models and risk rules can price it.
    """
    MAKER = 'maker'
    TAKER = 'taker'


# ----- Order-book market data ----- #

@dataclass(frozen=True, slots=True)
class BookLevel:
    """One price level of an order book. ``quantity`` is the displayed size at ``price``."""
    price: float
    quantity: float

    def __post_init__(self) -> None:
        if not isinstance(self.price, (int, float)) or not isinstance(self.quantity, (int, float)):
            raise TypeError("BookLevel price and quantity must be numeric")
        if self.price <= 0.0:
            raise ValueError("BookLevel price must be > 0")
        if self.quantity < 0.0:
            raise ValueError("BookLevel quantity must be >= 0")


@dataclass(frozen=True, slots=True)
class OrderBook:
    """
    The visible order book of one symbol at one instant.

    ``bids`` are ordered best first (descending price) and ``asks`` best first (ascending price).
    Derived fields (``best_bid``, ``best_ask``, ``spread``, ``mid_price``, ``spread_bps``,
    ``bid_quantity``, ``ask_quantity``) are computed once on construction so a strategy that sees
    thousands of books per session pays for them once.

    ``sequence`` is the feed's sequence number when it has one; ``checksum`` is a content hash a
    recorder may attach so identical consecutive books can be recognised without comparing levels.
    """
    time_stamp: datetime
    symbol: str
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    sequence: int | None = None
    checksum: str | None = None

    best_bid: BookLevel | None = field(init=False, repr=False, compare=False, hash=False)
    best_ask: BookLevel | None = field(init=False, repr=False, compare=False, hash=False)
    spread: float | None = field(init=False, repr=False, compare=False, hash=False)
    mid_price: float | None = field(init=False, repr=False, compare=False, hash=False)
    spread_bps: float | None = field(init=False, repr=False, compare=False, hash=False)
    bid_quantity: float = field(init=False, repr=False, compare=False, hash=False)
    ask_quantity: float = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self) -> None:
        bids = tuple(self.bids)
        asks = tuple(self.asks)
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)

        for previous, current in zip(bids, bids[1:]):
            if current.price >= previous.price:
                raise ValueError("OrderBook bids must be ordered best first (descending price)")
        for previous, current in zip(asks, asks[1:]):
            if current.price <= previous.price:
                raise ValueError("OrderBook asks must be ordered best first (ascending price)")

        best_bid = bids[0] if bids else None
        best_ask = asks[0] if asks else None
        spread = None if best_bid is None or best_ask is None else best_ask.price - best_bid.price
        mid_price = None if best_bid is None or best_ask is None else (best_bid.price + best_ask.price) / 2.0
        spread_bps = None if not mid_price or spread is None else spread / mid_price * 10_000.0

        object.__setattr__(self, "best_bid", best_bid)
        object.__setattr__(self, "best_ask", best_ask)
        object.__setattr__(self, "spread", spread)
        object.__setattr__(self, "mid_price", mid_price)
        object.__setattr__(self, "spread_bps", spread_bps)
        object.__setattr__(self, "bid_quantity", float(sum(level.quantity for level in bids)))
        object.__setattr__(self, "ask_quantity", float(sum(level.quantity for level in asks)))

    def is_two_sided(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None

    def is_crossed(self) -> bool:
        """True when the best ask is at or below the best bid (a locked or crossed book)."""
        return self.is_two_sided() and self.best_ask.price <= self.best_bid.price

    def imbalance(self, depth: int = 5) -> float | None:
        """
        (bid quantity - ask quantity) / (bid quantity + ask quantity) over the top ``depth`` levels.

        +1.0 is all bids, -1.0 is all asks, ``None`` when both sides are empty.
        """
        if depth <= 0:
            raise ValueError("depth must be > 0")
        bid_quantity = sum(level.quantity for level in self.bids[:depth])
        ask_quantity = sum(level.quantity for level in self.asks[:depth])
        total = bid_quantity + ask_quantity
        if total <= 0.0:
            return None
        return (bid_quantity - ask_quantity) / total

    def replace(self, **changes: Any) -> "OrderBook":
        """Return a copy with the given init fields changed; derived fields are recomputed."""
        return _dataclass_replace(self, **changes)

    def __str__(self) -> str:
        return (
            f"OrderBook {self.symbol} @ {self.time_stamp}: "
            f"bid {self.best_bid.price if self.best_bid else None} x {self.best_bid.quantity if self.best_bid else None}, "
            f"ask {self.best_ask.price if self.best_ask else None} x {self.best_ask.quantity if self.best_ask else None}, "
            f"{len(self.bids)} bid / {len(self.asks)} ask levels"
        )


@dataclass(frozen=True, slots=True)
class PublicTrade:
    """
    One print from the public tape.

    ``time_stamp`` is the exchange time of the trade. ``observed_at`` is when this process saw it,
    which matters for replay: a resting order may be filled at ``time_stamp`` but a strategy may
    only react from ``observed_at``. ``None`` means the two are the same.

    ``aggressor`` is the side of the taker (``BUY`` if the trade lifted an offer) when the feed says.
    """
    time_stamp: datetime
    symbol: str
    price: float
    quantity: float
    trade_id: str | None = None
    aggressor: SignalType | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.price, (int, float)) or not isinstance(self.quantity, (int, float)):
            raise TypeError("PublicTrade price and quantity must be numeric")
        if self.price <= 0.0:
            raise ValueError("PublicTrade price must be > 0")
        if self.quantity <= 0.0:
            raise ValueError("PublicTrade quantity must be > 0")
        if self.aggressor is not None and not isinstance(self.aggressor, SignalType):
            raise TypeError("PublicTrade aggressor must be a SignalType or None")

    @property
    def effective_observed_at(self) -> datetime:
        return self.time_stamp if self.observed_at is None else self.observed_at


MarketEvent = Union[Bar, OrderBook, PublicTrade]


def _coerce_liquidity(value: Any) -> "Liquidity | None":
    if value is None or isinstance(value, Liquidity):
        return value
    if isinstance(value, str):
        return Liquidity(value.lower())
    raise TypeError("liquidity must be a Liquidity, its string value, or None")


def _validate_limit_price(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)):
        raise TypeError("limit_price must be numeric or None")
    if value <= 0:
        raise ValueError("limit_price must be > 0")


@dataclass(frozen=True, slots=True)
class Signal:
    time_stamp: datetime
    symbol: str
    signal_type: SignalType | None
    ref_price: float
    confidence: float = 1.0
    quantity: float | None = None
    metadata: dict[str, Any] | None = None
    limit_price: float | None = None
    liquidity: Liquidity | None = None

    def __post_init__(self):
        if not isinstance(self.signal_type, SignalType):
            raise TypeError("Signal type must be a SignalType")

        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("Confidence must be between 0.0 and 1.0")

        if self.ref_price is not None and not isinstance(self.ref_price, (int, float)):
            raise TypeError("Price must be a number or None")

        _validate_limit_price(self.limit_price)
        object.__setattr__(self, "liquidity", _coerce_liquidity(self.liquidity))

    def with_metadata(self, **updates: Any) -> "Signal":
        """Return a copy whose metadata is the existing metadata merged with ``updates``."""
        merged = dict(self.metadata or {})
        merged.update(updates)
        return _dataclass_replace(self, metadata=merged)

    def __str__(self) -> str:
        return (
            f"Signal Type: {self.signal_type.value}, "
            +f"Timestamp: {self.time_stamp}, "
            +f"Symbol: {self.symbol}, "
            +f"Confidence: {self.confidence}"
            +(", Price: " + str(self.ref_price) if self.ref_price is not None else "")
            +(", Metadata: " + str(self.metadata) if self.metadata is not None else "")
        )

    def __repr__(self) -> str:
        return str(self)

@dataclass(frozen=True, slots=True)
class Order:
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float
    ref_price: float
    metadata: dict[str, Any] | None = None
    limit_price: float | None = None
    liquidity: Liquidity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.quantity, (int, float)):
            raise TypeError("Order quantity must be numeric")

        if self.quantity <= 0:
            raise ValueError("Order quantity must be > 0")

        if not isinstance(self.ref_price, (int, float)):
            raise TypeError("Order ref_price must be numeric")

        _validate_limit_price(self.limit_price)
        object.__setattr__(self, "liquidity", _coerce_liquidity(self.liquidity))

    @property
    def declared_limit_price(self) -> float | None:
        """
        The limit the strategy asked for: the ``limit_price`` field, else ``metadata["limit_price"]``
        (the pre-field convention, still honoured for old signal logs), else ``None`` for a market order.
        """
        if self.limit_price is not None:
            return float(self.limit_price)
        if self.metadata is not None:
            value = self.metadata.get("limit_price")
            if value is not None:
                return float(value)
        return None

    @property
    def is_limit(self) -> bool:
        return self.declared_limit_price is not None

    @property
    def effective_limit_price(self) -> float:
        """The price a simulation should use: the declared limit, otherwise ``ref_price``."""
        declared = self.declared_limit_price
        return float(self.ref_price) if declared is None else declared

    def __str__(self) -> str:
        return (
                f"Side: {self.side.value}, "
                +f"Timestamp: {self.time_stamp}, "
                +f"Symbol: {self.symbol}, "
                +f"Quantity: {self.quantity}, "
                +f"Price: {self.ref_price}"
                +(", Metadata: " + str(self.metadata) if self.metadata is not None else "")
        )


@dataclass(slots=True)
class Trade:
    id: int
    symbol: str
    signal_type: SignalType
    entry_price: float
    size: float
    open_time: datetime
    close_time: datetime | None = None
    realised_pnl: float | None = None

    def unrealised_pnl(self, current_price: float) -> float:
        # PnL is not applicable for trades where no operation was performed
        if self.signal_type is None:
            return 0.0

        # sign of pnl for BUY is always +1 (profit when price rises), for SELL is always -1 (profit when price falls)
        sign: int = 1 if self.signal_type is SignalType.BUY else -1
        return sign * (current_price - self.entry_price) * self.size

@dataclass(slots=True)
class Fill:
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float
    price: float

    order_id: str | None = None
    broker_order_id: str | None = None  # For things like IBKR IDs
    execution_id: str | None = None  # The venue's id for this execution, unique and stable
    commission: float | None = None
    liquidity: Liquidity | None = None


@dataclass(frozen=True, slots=True)
class OpenOrderView:
    """
    What a strategy or risk rule is allowed to know about one of its own resting orders.

    A read-only view produced by brokers that implement ``OpenOrderQueries``; never the broker's
    mutable state.
    """
    broker_order_id: str
    order: Order
    remaining_quantity: float
    submitted_at: datetime
    limit_price: float | None = None
    liquidity: Liquidity | None = None
    queue_ahead_quantity: float | None = None

    @property
    def symbol(self) -> str:
        return self.order.symbol

    @property
    def side(self) -> SignalType:
        return self.order.side

    @property
    def notional(self) -> float:
        price = self.limit_price if self.limit_price is not None else self.order.effective_limit_price
        return float(price) * float(self.remaining_quantity)


@dataclass(slots=True)
class OpenOrderState:
    """Deprecated: superseded by ``OpenOrderView``. Retained for one release."""
    broker_order_id: str
    order: Order
    submitted_at: datetime
    remaining_qty: float

    def __post_init__(self) -> None:
        warnings.warn(
            "OpenOrderState is deprecated; use OpenOrderView.",
            DeprecationWarning,
            stacklevel=2,
        )


@dataclass(frozen=True, slots=True)
class BacktestKPMs:
    total_trades: int
    total_wins: int
    total_losses: int
    win_rate_pct: float
    realised_pnl: float
    unrealised_pnl: float
    total_pnl: float

    # Risk-adjusted metrics; None when the equity path cannot support them.
    max_drawdown: float | None = None
    annualized_return_pct: float | None = None
    return_volatility_pct: float | None = None
    downside_volatility_pct: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None

    def as_dict(self) -> dict[str, float | int | None]:
        return asdict(self)

    def __str__(self) -> str:
        from ..metrics.risk_adjusted import format_metric

        return (
            f"Total Trades: {self.total_trades},\n"
            +f"Total Wins: {self.total_wins},\n"
            +f"Total Losses: {self.total_losses},\n"
            +f"Win Rate (%): {self.win_rate_pct:.2f},\n"
            +f"Realised pnl: {self.realised_pnl:.2f},\n"
            +f"Unrealised pnl: {self.unrealised_pnl:.2f},\n"
            +f"Total pnl: {self.total_pnl:.2f},\n"
            +f"Max drawdown (%): {format_metric(None if self.max_drawdown is None else self.max_drawdown * 100.0)},\n"
            +f"Annualised return (%): {format_metric(self.annualized_return_pct)},\n"
            +f"Sharpe: {format_metric(self.sharpe_ratio)},\n"
            +f"Sortino: {format_metric(self.sortino_ratio)},\n"
            +f"Calmar: {format_metric(self.calmar_ratio)}"
        )

    def __repr__(self) -> str:
        return str(self)


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time_stamp: datetime
    equity: float
    realised_pnl: float
    unrealised_pnl: float
    num_open_trades: int
