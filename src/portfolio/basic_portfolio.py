from datetime import datetime
from typing import Iterable

from core.types import Trade, EquityPoint, Signal, SignalType
from portfolio.portfolio_base import Portfolio


class BasicPortfolio(Portfolio):
    """
    A basic portfolio implementation which handles opening and closing trades. Note that it assumes liquid markets.
    """
    def __init__(self, init_capital: float = 10_000.0) -> None:
        self._init_capital: float = init_capital

        # Trades are uniquely identified by their id field but also by their symbol (cant close a trade of another symbol)
        # Symbol -> Trade ID -> Trade
        self._open_trades: dict[str, dict[int, Trade]] = {}
        self._closed_trades: list[Trade] = []

        self._next_trade_id: int = 0 # start indexing trades from 0
        self._realised_pnl: float = 0.0
        self._equity_points: list[EquityPoint] = []


    async def process_signal(self, signal: Signal) -> None:
        signal_type: SignalType = signal.signal_type

        if signal is None or signal_type is None:
            return

        if signal_type == SignalType.BUY:
            self._open(signal)
        elif signal_type == SignalType.SELL:
            self._close(signal)
        else:
            raise ValueError(f"Unsupported signal type: {signal_type}")


    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        unrealised_pnl: float = 0.0

        for symbol, trades in self._open_trades.items():
            if not trades:
                continue

            if symbol not in prices:
                raise KeyError(f"Price for symbol {symbol} not provided in prices dict.")

            current_price: float = prices[symbol]
            for trade in trades.values():
                unrealised_pnl += trade.unrealised_pnl(current_price)

        equity = self._init_capital + self._realised_pnl + unrealised_pnl

        self._equity_points.append(
            EquityPoint(
                time_stamp= time_stamp,
                equity= equity,
                realised_pnl= self._realised_pnl,
                unrealised_pnl= unrealised_pnl,
                num_open_trades=sum(len(t) for t in self._open_trades.values()),
            )
        )


    @property
    def closed_trades(self) -> Iterable[Trade]:
        return self._closed_trades


    @property
    def equity_curve(self) -> Iterable[EquityPoint]:
        return self._equity_points


    @property
    def realised_pnl(self) -> float:
        return self._realised_pnl


    # Helpers -------
    def _open(self, signal: Signal) -> None:
        if signal.quantity is None:
            raise ValueError("Signal quantity cannot be None")

        trade_id: int = self._next_trade_id
        self._next_trade_id += 1

        trade = Trade(
            id=trade_id,
            symbol=signal.symbol,
            signal_type=SignalType.BUY,
            entry_price=signal.price,
            size=signal.quantity,
            open_time=signal.time_stamp
        )
        if signal.symbol not in self._open_trades:
            self._open_trades[signal.symbol] = {}

        self._open_trades[signal.symbol][trade_id] = trade


    def _close(self, signal: Signal) -> None:
        if signal.quantity is None:
            raise ValueError("Signal quantity cannot be None")

        symbol: str = signal.symbol
        trades: dict = self._open_trades.get(symbol)
        if not trades:
            # Ideally this should raise an error, but strategies might not be that smart
            # raise RuntimeError(f"SELL signal received for {symbol}, but no open trades exist.")
            return

        close_qty: float = signal.quantity
        close_price: float = signal.price
        time: datetime = signal.time_stamp

        trade_ids_in_order = sorted(trades.keys(), reverse=True) # LIFO

        for trade_id in trade_ids_in_order: # Close multiple trades if needed, in order to satisfy quantity
            if close_qty <= 0:
                break

            trade = trades[trade_id]

            if trade.size <= close_qty:
                # Close entire trade
                trade.close_time = time
                trade.realised_pnl = trade.unrealised_pnl(close_price)

                self._realised_pnl += trade.realised_pnl
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
                    size=close_qty,
                    open_time=trade.open_time,
                    close_time=time,
                )
                closed_trade.realised_pnl = (close_price - trade.entry_price) * close_qty

                self._closed_trades.append(closed_trade)
                self._realised_pnl += closed_trade.realised_pnl

                trade.size -= close_qty
                close_qty = 0

        # close_qty should be 0 here, we could raise an error if not
