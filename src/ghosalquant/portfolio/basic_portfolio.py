from datetime import datetime
from typing import Iterable

from ..core.types import Trade, EquityPoint, Order, SignalType, Bar
from ..portfolio.portfolio_base import Portfolio


class BasicPortfolio(Portfolio):
    """
    A basic portfolio implementation which handles opening and closing trades. Note that it assumes liquid markets.
    """

    def __init__(
            self,
            init_capital: float = 10_000.0,
            fee_rate: float = 0.0015, # 0.15% per trade - 15 bps
            min_fee: float = 0.0 # Floor
    ) -> None:
        self._init_capital: float = init_capital
        self._cash: float = init_capital

        self._fee_rate: float = float(fee_rate)
        self._min_fee: float = float(min_fee)
        self._fees_paid: float = 0.0

        # Trades are uniquely identified by their id field but also by their symbol (cant close a trade of another symbol)
        # Symbol -> Trade ID -> Trade
        self._open_trades: dict[str, dict[int, Trade]] = {}
        self._closed_trades: list[Trade] = []

        self._next_trade_id: int = 0 # start indexing trades from 0
        self._realised_pnl: float = 0.0
        self._equity_points: list[EquityPoint] = []
        self._last_prices: dict[str, float] = {}

    async def process_order(self, order: Order) -> None:
        order_type: SignalType = order.side

        if order_type is None:
            return

        if order.quantity is None:
            raise ValueError("Order quantity cannot be None")

        if order_type == SignalType.BUY:
            self._open(order)
        elif order_type == SignalType.SELL:
            self._close(order)
        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        self.snapshot_equity(
            time_stamp= order.time_stamp,
            prices=self._last_prices
        )

    def update_equity(self, bar: Bar): # Called by execution system once per bar
        self._last_prices[bar.symbol] = float(bar.close)
        self.snapshot_equity(
            time_stamp=bar.time_stamp,
            prices=self._last_prices
        )

    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        positions_value: float = 0.0
        unrealised_pnl: float = 0.0

        for symbol, trades in self._open_trades.items():
            if not trades:
                continue

            if symbol not in prices:
                raise KeyError(f"Price for symbol {symbol} not provided in prices dict.")

            current_price: float = float(prices[symbol])
            for trade in trades.values():
                positions_value += trade.size * current_price
                unrealised_pnl += trade.unrealised_pnl(current_price)

        equity = self._cash + positions_value

        point = EquityPoint(
            time_stamp=time_stamp,
            equity=equity,
            realised_pnl=self._realised_pnl,
            unrealised_pnl=unrealised_pnl,
            num_open_trades=sum(len(t) for t in self._open_trades.values()),
        )

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
    def final_balance(self) -> float:
        if not self._equity_points:
            return self._init_capital
        return float(self._equity_points[-1].equity)

    @property
    def fees_paid(self) -> float:
        return self._fees_paid

    # Helpers -------
    def _calc_fee(self, notional: float) -> float:
        fee = abs(float(notional)) * self._fee_rate
        if fee < self._min_fee:
            fee = self._min_fee
        return fee

    def _open(self, order: Order) -> None:
        if order.quantity is None:
            raise ValueError("Order quantity cannot be None")

        notional: float = float(order.price) * float(order.quantity)
        fee: float = self._calc_fee(notional)
        total_cost: float = notional + fee

        if total_cost > self._cash: # We reject rather than clip size
            #print(f"[Portfolio]: Not enough cash to execute BUY order for {order.symbol}: cost={cost:.2f} cash={self._cash:.2f} qty={order.quantity} price={order.price}")
            return

        self._cash -= total_cost
        self._fees_paid += fee
        self._realised_pnl -= fee

        trade_id: int = self._next_trade_id
        self._next_trade_id += 1

        trade = Trade(
            id=trade_id,
            symbol=order.symbol,
            signal_type=SignalType.BUY,
            entry_price=order.price,
            size=order.quantity,
            open_time=order.time_stamp
        )
        if order.symbol not in self._open_trades:
            self._open_trades[order.symbol] = {}

        self._open_trades[order.symbol][trade_id] = trade

    def _close(self, order: Order) -> None:
        if order.quantity is None:
            raise ValueError("Order quantity cannot be None")

        symbol: str = order.symbol
        trades: dict = self._open_trades.get(symbol)
        if not trades:
            # Ideally this should raise an error, but strategies might not be that smart
            # raise RuntimeError(f"SELL order received for {symbol}, but no open trades exist.")
            return

        close_qty: float = order.quantity
        close_price: float = order.price
        time: datetime = order.time_stamp

        trade_ids_in_order = sorted(trades.keys(), reverse=True)  # LIFO

        for trade_id in trade_ids_in_order:  # Close multiple trades if needed, in order to satisfy quantity
            if close_qty <= 0:
                break

            trade = trades[trade_id]

            # Amount we are closing form this trade
            qty_to_close: float = min(trade.size, close_qty)

            # fee on close
            notional = qty_to_close * close_price
            fee: float = self._calc_fee(notional)

            # Find realised pnl
            realised = (close_price - trade.entry_price) * qty_to_close - fee
            self._realised_pnl += realised

            # Cash received from sale
            self._cash += notional - fee

            # Fees tracking
            self._fees_paid += fee

            if trade.size <= close_qty:
                # Close entire trade
                trade.close_time = time
                trade.realised_pnl = trade.unrealised_pnl(close_price) - fee
                self._closed_trades.append(trade)

                close_qty -= trade.size
                del trades[trade_id]
            else:
                # Partially close trade, and make a new closed trade record
                new_trade_id: int = self._next_trade_id
                self._next_trade_id += 1

                closed_trade = Trade(
                    id=new_trade_id,
                    symbol=trade.symbol,
                    signal_type=SignalType.SELL,
                    entry_price=trade.entry_price,
                    size=qty_to_close,
                    open_time=trade.open_time,
                    close_time=time,
                )
                closed_trade.realised_pnl = (close_price - trade.entry_price) * qty_to_close - fee

                self._closed_trades.append(closed_trade)

                trade.size -= qty_to_close
                close_qty -= qty_to_close
