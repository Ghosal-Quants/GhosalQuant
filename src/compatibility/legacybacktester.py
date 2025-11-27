import asyncio
from datetime import datetime

import pandas as pd
from pathlib import Path

from backtester.backtester import Backtester
from compatibility.trade import Trade
from core.types import Signal, SignalType, Trade
from data_layer.dataframe_datasource import DataFrameDataSource
from execution.sinks import DataFrameSink, BacktesterSink, PrintSink
from execution.strategy_base import Strategy

from util.backtester_utils import calculate_stats, output_to_excel


PROJECT_ROOT = Path(__file__).resolve().parents[2]

# TODO: Move this to DataFrameBacktester, and make the logic streamed with handle_signal() instead of running once at execution level, then again here (not a fan of two passes + not scalable for real time / streamed bars)

"""
Note for anyone reading this:

This is older logic, and its just kept here for compatibility. I know it's hella inefficient but its also 2 am rn.

(See the above TODO for the intended changes)
"""

class LegacyBacktester(Backtester):
    def __init__(
            self, df: pd.DataFrame,
            symbol: str,
            strategy: Strategy,
            init_capital: int = 10000
    ):
        self.df_equity = None
        self.df_results = None
        self._df = df
        self.strategy = strategy

        self._next_trade_id: int = 0 # start indexing trades from 0
        self._init_capital = init_capital

        # Trades are uniquely identified by their id field but also by their symbol (cant close a trade of another symbol)
        # Symbol -> Trade ID -> Trade()
        self._open_trades: dict[str, dict[int, Trade]] = {}
        self._closed_trades: list[Trade] = []

        sink_chain = PrintSink(next_sink=BacktesterSink(self))

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=self._df,
                symbol= symbol,
            ),
            signal_destination=sink_chain
        )

    async def handle_signal(self, signal: Signal) -> None:
        """
        Called for every signal emitted by BacktesterSink (i.e. after receiving it from the Strategy)
        """
        raise RuntimeError("How did we get here? (LegacyBacktester shouldn't handle signals directly)")
        # TODO: WIP - Stream calculations

        if signal.signal_type == SignalType.NOOP:
            return

        if signal.quantity is None:
            raise ValueError("Signal quantity cannot be None")

        # Open trade
        if signal.signal_type == SignalType.BUY:
            trade_id: int = self._next_trade_id
            self._next_trade_id += 1

            if signal.symbol not in self._open_trades:
                self._open_trades[signal.symbol] = {}

            trade = Trade(
                id=trade_id,
                symbol=signal.symbol,
                signal_type=signal.signal_type,
                entry_price=signal.price,
                size=signal.quantity if signal.quantity is not None else 1,
                open_time=signal.time_stamp
            )
            self._open_trades[signal.symbol][trade.id] = trade
            return

        # Close trade
        if signal.signal_type == SignalType.SELL:
            if signal.symbol not in self._open_trades or not self._open_trades[signal.symbol]:
                # Ideally this should raise an error, but strategies might not be that smart
                # raise RuntimeError(f"SELL signal received for {symbol}, but no open trades exist.")
                return

            trade_id = next(reversed(self._open_trades[signal.symbol]))
            trade = self._open_trades[signal.symbol].pop(trade_id)

            trade.close_time = signal.time_stamp
            trade.realised_pnl = trade.unrealised_pnl(signal.price)

            self._closed_trades.append(trade) # Keep track of closed trades

            return

        # In case new signal types are added in the future which need handling
        raise TypeError(f"Unknown signal type: {signal.signal_type}")

    def run(self):
        """
        Run the backtests
        """

        asyncio.run(self.execution_system.run())

        # Create two empty lists: one for currently active trades, one for finished trades
        open_trades = []
        closed_trades = []

        # initial capital and equity tracking
        init_capital = 10000
        current_equity = init_capital

        # track how our account equity changes per time
        equity_data = []

        # Go through every row (candle) in the data.

        for index, row in self._df.iterrows():
                
            # If a trading signal is found in the current row:
            if row.SIGNAL != SignalType.NOOP.value:
                # Start a new trade object and add it to the open list
                open_trades.append(Trade(row))  
            
            # For every trade currently open:
            for ot in open_trades:
                # Update the trade with the current market data (checks for profit/loss targets, etc.).
                ot.update(row)
                # If the trade update causes it to close:
                if ot.running == False:
                    # Move the now closed trade to the closed list.
                    closed_trades.append(ot)
            
            # realized pnl is sum of pnl from closed trades - update after appended
            ttl_realized_pnl = 0

            for trade in closed_trades:
                ttl_realized_pnl += trade.pnl
            
            # Remove the trades that just closed from the open list.
            open_trades = [x for x in open_trades if x.running == True]

            # unrealized pnl from currently open trades
            unrealized_pnl = 0

            for ot in open_trades:
                # if buy, pnl = (current price - entry price) * size
                # if sell, pnl = (entry price - current price) * size

                if ot.SIGNAL == SignalType.BUY.value: #buy
                    unrealized_pnl += (row.Close - ot.entry_price)
                elif ot.SIGNAL == SignalType.SELL.value: #sell
                    unrealized_pnl += (ot.entry_price - row.Close)
            
            current_equity = init_capital + ttl_realized_pnl + unrealized_pnl

            equity_data.append({
                "time": index, # ts is our index
                "equity": current_equity,
                "open_trades": len(open_trades)
            })


        # Convert the final list of closed trades into a readable DataFrame (table) for results.
        self.df_results = pd.DataFrame.from_dict([vars(x) for x in closed_trades]) 
        self.df_equity = pd.DataFrame(equity_data)
        # Trade.result = 1|-1

        calculate_stats(df_results=self.df_results)

        # Save the final results table to an Excel file.
        output_to_excel(df_results=self.df_results)
