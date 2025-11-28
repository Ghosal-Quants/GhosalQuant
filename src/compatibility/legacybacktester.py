from typing import Callable
import pandas as pd
from pathlib import Path

from compatibility.trade import Trade

from util.backtester_utils import calculate_stats, output_to_excel
from util.deprecation import deprecated

# ===============================================================================================
# NOTICE: LEGACY BACKTESTER
#
# This is older logic, and its just kept here for compatibility.
#
# TODO: Move logic to DataFrameBacktester, and stream it with handle_signal() instead of running once at execution
#  level, then again here (not a fan of two passes + not scalable for real time / streamed bars)
# ===============================================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Legacy strategy function type
# Define the type of the function being passed in:
# It takes two floats as arguments, and returns a float.
strategy_func = Callable[[pd.DataFrame], pd.DataFrame]

@deprecated("LegacyBacktester's logic is outdated. Please use DataFrameBacktester instead.")
class LegacyBacktester:
    def __init__(self, df, strategy: strategy_func):
        self.df = df
        self.strategy = strategy

        # prepare the data
        self.prepare_data()

        # apply strategy
        self.apply_strategy()

    def prepare_data(self):
        """
        Will later handle the merging logic
        """

        # ADD BUY/SELL COLUMNs
        self.df["BUY"] = 0
        self.df["SELL"] = 0


        pass

    def apply_strategy(self):
        """
        Apply strategy to self.df

        Should give back a df with BUY/SELL
        """

        # Apply the function - do we need to copy?
        self.df = self.strategy(self.df.copy())

    def run_backtest(self):
        """
        Run the backtests
        """

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
            if row.SIGNAL != 0:
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

                if ot.SIGNAL == 1: #buy
                    unrealized_pnl += (row.Close - ot.entry_price)
                elif ot.SIGNAL == -1:
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

        calculate_stats(self.df_results)

        # Save the final results table to an Excel file.
        output_to_excel(self.df_results)
