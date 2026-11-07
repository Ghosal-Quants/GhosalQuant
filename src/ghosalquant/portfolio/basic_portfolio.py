from datetime import datetime
from typing import Iterable, Mapping

from ..core.contracts import ContractRegistry
from ..core.types import Trade, EquityPoint, Fill, SignalType, Bar, CashFlow
from ..portfolio.portfolio_base import Portfolio

_EPS = 1e-12


class BasicPortfolio(Portfolio):
    """
    A basic portfolio implementation which handles opening and closing trades.
    """

    def __init__(
            self,
            init_capital: float = 10_000.0,
            contracts: ContractRegistry | None = None,
    ) -> None:
        self._contracts = contracts if contracts is not None else ContractRegistry()
        self._init_capital: float = init_capital
        self._cash: float = init_capital

        self._fees_paid: float = 0.0
        self._cash_flows: float = 0.0

        # Symbol -> Trade ID -> Trade
        self._open_trades: dict[str, dict[int, Trade]] = {}
        self._closed_trades: list[Trade] = []

        self._next_trade_id: int = 0 # start indexing trades from 0
        self._realised_pnl: float = 0.0
        self._equity_points: list[EquityPoint] = []
        self._peak_equity: float = init_capital
        self._last_prices: dict[str, float] = {}
        self._accounting_version: int = 0
        self._open_cost_by_symbol: dict[str, float] = {}
        self._open_qty_by_symbol: dict[str, float] = {}
        self._applied_execution_ids: set[str] = set()
        self._fill_count: int = 0

    def adopt(
            self,
            cash: float,
            positions: Mapping[str, tuple[float, float]] | None = None,
            *,
            time_stamp: datetime | None = None,
    ) -> None:
        """
        Start from what the account actually holds instead of from ``init_capital``.

        A live session inherits cash and positions that this process did not create, and every
        capital-aware risk rule sizes from equity. Starting flat means sizing against a number
        nobody chose. ``positions`` maps a symbol to its quantity and average entry price.
        """
        if self._closed_trades or any(self._open_trades.values()):
            raise RuntimeError("adopt() must be called before any fills are processed")
        self._cash = float(cash)
        for symbol, (quantity, entry_price) in (positions or {}).items():
            if abs(quantity) <= _EPS:
                continue
            self._record_open_trade(
                Trade(
                    id=self._take_trade_id(),
                    symbol=str(symbol),
                    signal_type=SignalType.BUY if quantity > 0.0 else SignalType.SELL,
                    entry_price=float(entry_price),
                    size=abs(float(quantity)),
                    open_time=time_stamp or datetime.now(),
                )
            )
            self._last_prices[symbol] = float(entry_price)
        # Without a snapshot, current_equity still answers with the init_capital being replaced.
        self.snapshot_equity(
            time_stamp=time_stamp or datetime.now(), prices=self._last_prices
        )
        self._init_capital = self.current_equity
        self._peak_equity = self._init_capital
        self._accounting_version += 1

    async def process_fill(self, fill: Fill) -> None:
        # Applying one execution twice invents a position the account never held.
        if fill.execution_id is not None:
            if fill.execution_id in self._applied_execution_ids:
                return
            self._applied_execution_ids.add(fill.execution_id)

        cash_before = self._cash
        quantity_before = self.open_qty_by_symbol.get(fill.symbol, 0.0)
        # A new lot must be marked from the fill that created it, not from a
        # stale price cached before the execution.
        self._last_prices[fill.symbol] = float(fill.price)
        if fill.side not in (SignalType.BUY, SignalType.SELL):
            raise ValueError(f"Unsupported fill side: {fill.side}")
        self._apply(fill)

        quantity_after = self.open_qty_by_symbol.get(fill.symbol, 0.0)
        if (
            abs(self._cash - cash_before) > 1e-12
            or abs(quantity_after - quantity_before) > 1e-12
        ):
            self._accounting_version += 1
            self._fill_count += 1
        self.snapshot_equity(
            time_stamp=fill.time_stamp,
            prices=self._last_prices
        )

    async def process_cash_flow(self, flow: CashFlow) -> None:
        """Book cash a venue paid or charged on a position: realised P&L, not a fee."""
        if abs(flow.amount) <= _EPS:
            return
        self._cash += flow.amount
        self._cash_flows += flow.amount
        self._realised_pnl += flow.amount
        self._accounting_version += 1
        self.snapshot_equity(time_stamp=flow.time_stamp, prices=self._last_prices)

    def mark(self, symbol: str, price: float, time_stamp: datetime) -> None:
        self._last_prices[symbol] = float(price)

    def update_equity(self, bar: Bar) -> None:
        self.mark(bar.symbol, bar.close, bar.time_stamp)
        self.snapshot_equity(
            time_stamp=bar.time_stamp,
            prices=self._last_prices
        )

    def average_entry_price(self, symbol: str) -> float | None:
        quantity = self._open_qty_by_symbol.get(symbol, 0.0)
        if abs(quantity) <= _EPS:
            return None
        # Cost is money and quantity is contracts, so the multiplier divides back out.
        return self._open_cost_by_symbol.get(symbol, 0.0) / (quantity * self._multiplier(symbol))

    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        positions_value: float = 0.0
        unrealised_pnl: float = 0.0

        for symbol, quantity in self._open_qty_by_symbol.items():
            if symbol not in prices:
                raise KeyError(f"Price for symbol {symbol} not provided in prices dict.")

            current_price: float = float(prices[symbol])
            value = quantity * current_price * self._multiplier(symbol)
            positions_value += value
            unrealised_pnl += value - self._open_cost_by_symbol.get(symbol, 0.0)

        equity = self._cash + positions_value

        point = EquityPoint(
            time_stamp=time_stamp,
            equity=equity,
            realised_pnl=self._realised_pnl,
            unrealised_pnl=unrealised_pnl,
            num_open_trades=sum(len(t) for t in self._open_trades.values()),
        )

        if equity > self._peak_equity:
            self._peak_equity = equity

        if self._equity_points and self._equity_points[-1].time_stamp == time_stamp:
            self._equity_points[-1] = point
        else:
            self._equity_points.append(point)

    @property
    def closed_trades(self) -> Iterable[Trade]:
        return self._closed_trades

    @property
    def equity_curve(self) -> Iterable[EquityPoint]:
        return self._equity_points

    @property
    def realised_pnl(self) -> float:
        return self._realised_pnl

    @property
    def unrealised_pnl(self) -> float:
        if not self._equity_points:
            return 0.0
        return float(self._equity_points[-1].unrealised_pnl)

    @property
    def cash_balance(self) -> float:
        return float(self._cash)

    @property
    def current_equity(self) -> float:
        if not self._equity_points:
            return self._init_capital
        return float(self._equity_points[-1].equity)

    @property
    def peak_equity(self) -> float:
        return float(self._peak_equity)

    def reset_peak_equity(self) -> None:
        self._peak_equity = self.current_equity

    @property
    def open_position_count(self) -> int:
        return sum(len(trades) for trades in self._open_trades.values())

    @property
    def open_qty_by_symbol(self) -> dict[str, float]:
        return dict(self._open_qty_by_symbol)

    @property
    def open_trades(self) -> tuple[Trade, ...]:
        return tuple(trade for trades in self._open_trades.values() for trade in trades.values())

    @property
    def final_balance(self) -> float:
        if not self._equity_points:
            return self._init_capital
        return float(self._equity_points[-1].equity)

    @property
    def fees_paid(self) -> float:
        return self._fees_paid

    @property
    def cash_flows(self) -> float:
        """Net cash flows booked, received less charged."""
        return self._cash_flows

    @property
    def accounting_version(self) -> int:
        return self._accounting_version

    @property
    def fill_count(self) -> int:
        return self._fill_count

    @property
    def last_prices(self) -> dict[str, float]:
        return dict(self._last_prices)

    @property
    def open_cost_by_symbol(self) -> dict[str, float]:
        return dict(self._open_cost_by_symbol)

    # Helpers -------

    def _apply(self, fill: Fill) -> None:
        """
        Net a fill against the position: opening, reducing, or reversing through flat.

        A sell with nothing to sell opens a short rather than doing nothing, which is how selling
        premium works. Margin is not modelled here — what may be shorted is a risk rule's business.
        """
        wanted = float(fill.quantity)
        if wanted <= _EPS:
            return
        position = self._open_qty_by_symbol.get(fill.symbol, 0.0)
        signed = wanted if fill.side is SignalType.BUY else -wanted
        commission = fill.commission or 0.0

        if abs(position) <= _EPS or (position > 0.0) == (signed > 0.0):
            self._open(fill, wanted, commission)
            return

        reducing = min(wanted, abs(position))
        closed = self._close(fill, reducing, commission * reducing / wanted)
        leftover = wanted - closed
        if leftover > _EPS:
            self._open(fill, leftover, commission * leftover / wanted)

    def _open(self, fill: Fill, quantity: float, commission: float) -> None:
        long_side = fill.side is SignalType.BUY
        notional: float = fill.price * quantity * self._multiplier(fill.symbol)

        # A fill already happened at the broker: booking only what cash covers would leave the portfolio holding
        # less than the account. What may be bought on how much cash is a risk rule's business, as for shorts.
        self._cash += (-notional if long_side else notional) - commission
        self._fees_paid += commission
        self._realised_pnl -= commission

        self._record_open_trade(
            Trade(
                id=self._take_trade_id(),
                symbol=fill.symbol,
                signal_type=SignalType.BUY if long_side else SignalType.SELL,
                entry_price=fill.price,
                size=quantity,
                open_time=fill.time_stamp,
            ),
            notional=notional if long_side else -notional,
        )

    def _close(self, fill: Fill, quantity: float, commission: float) -> float:
        """Close against existing lots, newest first, returning how much was closed."""
        symbol: str = fill.symbol
        trades: dict | None = self._open_trades.get(symbol)
        if not trades or quantity <= _EPS:
            return 0.0

        multiplier: float = self._multiplier(symbol)
        close_price: float = fill.price
        remaining: float = quantity

        for trade_id in sorted(trades.keys(), reverse=True):  # LIFO
            if remaining <= _EPS:
                break

            trade = trades[trade_id]
            qty_to_close: float = min(trade.size, remaining)
            long_lot: bool = trade.signal_type is SignalType.BUY
            notional: float = qty_to_close * close_price * multiplier
            basis: float = qty_to_close * trade.entry_price * multiplier
            leg_commission: float = commission * (qty_to_close / quantity)

            self._open_qty_by_symbol[symbol] = self._open_qty_by_symbol.get(symbol, 0.0) + (
                -qty_to_close if long_lot else qty_to_close
            )
            self._open_cost_by_symbol[symbol] = self._open_cost_by_symbol.get(symbol, 0.0) + (
                -basis if long_lot else basis
            )

            self._cash += (notional if long_lot else -notional) - leg_commission
            realised = (notional - basis if long_lot else basis - notional) - leg_commission
            self._realised_pnl += realised
            self._fees_paid += leg_commission

            if trade.size - qty_to_close <= _EPS:
                trade.close_time = fill.time_stamp
                trade.realised_pnl = trade.unrealised_pnl(close_price) * multiplier - leg_commission
                self._closed_trades.append(trade)
                del trades[trade_id]
            else:
                closed_trade = Trade(
                    id=self._take_trade_id(),
                    symbol=trade.symbol,
                    signal_type=trade.signal_type,
                    entry_price=trade.entry_price,
                    size=qty_to_close,
                    open_time=trade.open_time,
                    close_time=fill.time_stamp,
                )
                closed_trade.realised_pnl = realised
                self._closed_trades.append(closed_trade)
                trade.size -= qty_to_close

            remaining -= qty_to_close

        if not trades:
            self._open_trades.pop(symbol, None)
            self._open_qty_by_symbol.pop(symbol, None)
            self._open_cost_by_symbol.pop(symbol, None)
        return quantity - remaining

    def _take_trade_id(self) -> int:
        trade_id = self._next_trade_id
        self._next_trade_id += 1
        return trade_id

    def _multiplier(self, symbol: str) -> float:
        return self._contracts.get(symbol).multiplier

    def _record_open_trade(self, trade: Trade, *, notional: float | None = None) -> None:
        long_side = trade.signal_type is SignalType.BUY
        size = float(trade.size) if long_side else -float(trade.size)
        cost = (
            trade.entry_price * size * self._multiplier(trade.symbol)
            if notional is None
            else notional
        )
        self._open_trades.setdefault(trade.symbol, {})[trade.id] = trade
        self._open_qty_by_symbol[trade.symbol] = (
            self._open_qty_by_symbol.get(trade.symbol, 0.0) + size
        )
        self._open_cost_by_symbol[trade.symbol] = (
            self._open_cost_by_symbol.get(trade.symbol, 0.0) + cost
        )

