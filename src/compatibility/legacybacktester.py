import asyncio

import pandas as pd
from pathlib import Path

from backtester.backtester import Backtester
from compatibility.trade import Trade
from core.types import Signal, SignalType
from data_layer.dataframe_datasource import DataFrameDataSource
from execution.sinks import DataFrameSink
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
    def __init__(self, df: pd.DataFrame, symbol: str, strategy: Strategy):
        self.df_equity = None
        self.df_results = None
        self.df = df
        self.strategy = strategy

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=self.df,
                symbol= symbol,
            ),
            signal_destination=DataFrameSink(
                dataframe= self.df,
                timestamp_col=None,
                signal_col="SIGNAL",
                confidence_col="CONFIDENCE",
                quantity_col="QTY"
            )
        )

    async def handle_signal(self, signal: Signal) -> None:
        raise RuntimeError("How did we get here? (LegacyBacktester shouldn't handle signals directly)")

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

        for index, row in self.df.iterrows():
                
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
