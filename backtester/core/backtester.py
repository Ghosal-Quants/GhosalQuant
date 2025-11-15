from typing import Callable
import pandas as pd

# Define the type of the function being passed in:
# It takes two floats as arguments, and returns a float.
strategy_func = Callable[[pd.DataFrame], pd.DataFrame]


class Backtester:
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

        
        
        pass


    def run_backtest(self):

        """

        Create two empty lists: one for currently active trades, one for finished trades
        open_trades = []
        closed_trades = []

        Go through every row (candle) in the data.

        for index, row in self.df.iterrows():
                
            If a trading signal is found in the current row:
            if row.SIGNAL != 0:
                Start a new trade object and add it to the open list
                open_trades_m5.append(Trade(row))  
                    
            For every trade currently open:
            for ot in open_trades:
                Update the trade with the current market data (checks for profit/loss targets, etc.).
                ot.update(row)
                If the trade update causes it to close:
                if ot.running == False:
                    Move the now closed trade to the closed list.
                    closed_trades.append(ot)
                        
            Remove the trades that just closed from the open list.
            open_trades_m5 = [x for x in open_trades_m5 if x.running == True]

        Convert the final list of closed trades into a readable DataFrame (table) for results.
        self.df_results = pd.DataFrame.from_dict([vars(x) for x in closed_trades]) 

        Save the final results table to an Excel file.
        self.output_to_excel(self.df_results.copy())

        """

    def output_to_excel(self):
        """
        on the tin
        """
        pass
        

