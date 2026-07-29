from datetime import datetime
from typing import Iterable

from ..core.types import Trade, EquityPoint, Fill, SignalType, Bar
from ..portfolio.portfolio_base import Portfolio


class BasicPortfolio(Portfolio):
    """
    A basic portfolio implementation which handles opening and closing trades.
    """

    def __init__(
            self,
            init_capital: float = 10_000.0,
    ) -> None:
        self._init_capital: float = init_capital
        self._cash: float = init_capital

        self._fees_paid: float = 0.0

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

    async def process_fill(self, fill: Fill) -> None:
        cash_before = self._cash
        quantity_before = self.open_qty_by_symbol.get(fill.symbol, 0.0)
        # A new lot must be marked from the fill that created it, not from a
        # stale price cached before the execution.
        self._last_prices[fill.symbol] = float(fill.price)
        if fill.side == SignalType.BUY:
            self._open(fill)
        elif fill.side == SignalType.SELL:
            self._close(fill)
        else:
            raise ValueError(f"Unsupported fill side: {fill.side}")

        quantity_after = self.open_qty_by_symbol.get(fill.symbol, 0.0)
        if (
            abs(self._cash - cash_before) > 1e-12
            or abs(quantity_after - quantity_before) > 1e-12
        ):
            self._accounting_version += 1
        self.snapshot_equity(
            time_stamp=fill.time_stamp,
            prices=self._last_prices
        )

    def update_equity(self, bar: Bar) -> None:
        self._last_prices[bar.symbol] = float(bar.close)
        self.snapshot_equity(
            time_stamp=bar.time_stamp,
            prices=self._last_prices
        )

    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        positions_value: float = 0.0
        unrealised_pnl: float = 0.0

        for symbol, quantity in self._open_qty_by_symbol.items():
            if symbol not in prices:
                raise KeyError(f"Price for symbol {symbol} not provided in prices dict.")

            current_price: float = float(prices[symbol])
            positions_value += quantity * current_price
            unrealised_pnl += (
                quantity * current_price
                - self._open_cost_by_symbol.get(symbol, 0.0)
            )

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
    def final_balance(self) -> float:
        if not self._equity_points:
            return self._init_capital
        return float(self._equity_points[-1].equity)

    @property
    def fees_paid(self) -> float:
        return self._fees_paid

    @property
    def accounting_version(self) -> int:
        return self._accounting_version

    @property
    def last_prices(self) -> dict[str, float]:
        return dict(self._last_prices)

    @property
    def open_cost_by_symbol(self) -> dict[str, float]:
        return dict(self._open_cost_by_symbol)

    # Helpers -------

    def _open(self, fill: Fill) -> None:
        commission: float = fill.commission or 0.0
        notional: float = fill.price * fill.quantity
        total_cost: float = notional + commission

        if total_cost > self._cash:
            return

        self._cash -= total_cost
        self._fees_paid += commission
        self._realised_pnl -= commission

        trade_id: int = self._next_trade_id
        self._next_trade_id += 1

        trade = Trade(
            id=trade_id,
            symbol=fill.symbol,
            signal_type=SignalType.BUY,
            entry_price=fill.price,
            size=fill.quantity,
            open_time=fill.time_stamp,
        )
        self._open_trades.setdefault(fill.symbol, {})[trade_id] = trade
        self._open_qty_by_symbol[fill.symbol] = (
            self._open_qty_by_symbol.get(fill.symbol, 0.0)
            + float(fill.quantity)
        )
        self._open_cost_by_symbol[fill.symbol] = (
            self._open_cost_by_symbol.get(fill.symbol, 0.0) + notional
        )

    def _close(self, fill: Fill) -> None:
        symbol: str = fill.symbol
        trades: dict | None = self._open_trades.get(symbol)
        if not trades:
            return

        close_qty: float = fill.quantity
        close_price: float = fill.price
        commission: float = fill.commission or 0.0

        trade_ids_in_order = sorted(trades.keys(), reverse=True)  # LIFO

        for trade_id in trade_ids_in_order:  # Close multiple trades if needed, in order to satisfy quantity
            if close_qty <= 0:
                break

            trade = trades[trade_id]

            # Amount we are closing form this trade
            qty_to_close: float = min(trade.size, close_qty)
            self._open_qty_by_symbol[symbol] = max(
                0.0,
                self._open_qty_by_symbol.get(symbol, 0.0)
                - qty_to_close,
            )
            self._open_cost_by_symbol[symbol] = max(
                0.0,
                self._open_cost_by_symbol.get(symbol, 0.0)
                - trade.entry_price * qty_to_close,
            )

            leg_commission = commission * (qty_to_close / fill.quantity)

            notional = qty_to_close * close_price
            realised = (close_price - trade.entry_price) * qty_to_close - leg_commission
            self._realised_pnl += realised
            self._cash += notional - leg_commission
            self._fees_paid += leg_commission

            if trade.size <= close_qty:
                trade.close_time = fill.time_stamp
                trade.realised_pnl = trade.unrealised_pnl(close_price) - leg_commission
                self._closed_trades.append(trade)
                close_qty -= trade.size
                del trades[trade_id]
            else:
                new_trade_id: int = self._next_trade_id
                self._next_trade_id += 1

                closed_trade = Trade(
                    id=new_trade_id,
                    symbol=trade.symbol,
                    signal_type=SignalType.SELL,
                    entry_price=trade.entry_price,
                    size=qty_to_close,
                    open_time=trade.open_time,
                    close_time=fill.time_stamp,
                )
                closed_trade.realised_pnl = (close_price - trade.entry_price) * qty_to_close - leg_commission
                self._closed_trades.append(closed_trade)

                trade.size -= qty_to_close
                close_qty -= qty_to_close

        if not trades:
            self._open_qty_by_symbol.pop(symbol, None)
            self._open_cost_by_symbol.pop(symbol, None)
