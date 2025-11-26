from typing import Callable
import pandas as pd
from trade import Trade
from datetime import datetime
from pathlib import Path

# Define the type of the function being passed in:
# It takes two floats as arguments, and returns a float.
strategy_func = Callable[[pd.DataFrame], pd.DataFrame]


class Backtester:
    def __init__(self, df_hour_data, df_sec_data, strategy: strategy_func):

        # df hour and df sec has to have index as dt obj

        self.df_hour_data = df_hour_data
        self.df_sec_data = df_sec_data
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
        self.df_hour_data["BUY"] = 0
        self.df_hour_data["SELL"] = 0

        pass

    def apply_strategy(self):
        """
        Apply strategy to self.df
        
        Should give back a df with BUY/SELL
        """

        # Apply the function - do we need to copy?
        self.df_hour_data = self.strategy(self.df_hour_data.copy())
        
        pass


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

        # def 1 hr
        one_hour = pd.Timedelta(hours=1)

        # track how our account equity changes per time
        equity_data = []

        # Go through every row (candle) in the data.
        for index, row in self.df_hour_data.iterrows():
            # first check if there is even a need to zoom in on data
            if (len(open_trades)) > 0:
                
                # check for sl/tp
                need_to_zoom = False

                for ot in open_trades:
                    
                    # for long positions check if low <= sl or if high >= tp
                    if ot.SIGNAL == 1:
                        if (row.Low <= ot.SL) or (row.High >= ot.TP):
                            need_to_zoom = True
                            break
                    
                    # for short positions check if high has gone above sl or if low has gone below tp
                    elif ot.SIGNAL == -1:
                        
                        if (row.High >= ot.SL) or (row.Low <= ot.TP):
                            need_to_zoom = True
                            break
                
                # if zoom
                if (need_to_zoom):
                    bar_start = index # timestamp of current bar
                    bar_end = index + one_hour # next 1 hr, can check using iloc 1 position ahead?

                    # also by dt index
                    hour_sec_bars = self.df_sec_data.loc[bar_start:bar_end]

                    # iter through each sec in hr bar
                    for sec_index, sec_row in hour_sec_bars.iterrows():
                        # update w exact data
                        for ot in open_trades:
                            ot.update(sec_row)
                            # same logic as prev
                            if ot.running == False:
                                ot.exit_time = sec_index
                                closed_trades.append(ot)
                            
                        # Remove the trades that just closed from the open list.
                        open_trades = [x for x in open_trades if x.running == True]

                        # and if closed then break - check for all open trades
                        if (len(open_trades) == 0):
                            break
                
                # if we definitively know then continue
                else:
                    pass
            
            # then continue w previous logic                
            # If a trading signal is found in the current row:
            if row.SIGNAL != 0:
                # Start a new trade object and add it to the open list
                # if trade open then ignore new signals
                if len(open_trades) == 0:
                    open_trades.append(Trade(row))  
            
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

        self.calc_stats()

        # Save the final results table to an Excel file.
        self.output_to_excel()


    def output_to_excel(self):
        """
        on the tin
        """
        out_dir = Path("results/trades")

        # match up data
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # save in results/trades
        fn = f"trades_{timestamp}.xlsx"
        fp = out_dir / fn

        if self.df_results is not None:
            self.df_results.to_excel(fp, index=False)
            print(f"Saved trades to {fp}")
        else:
            print("No closed trades")
        
        if not self.df_equity.empty:
            fn_equity = f"equity_{timestamp}.xlsx"
            fp_equity = out_dir / fn_equity
            self.df_equity.to_excel(fp_equity, index=False)
            print(f"Saved equity to {fp_equity}")
    
    def calc_stats(self):
        """
        calculates kpms
        """

        if self.df_results is None:
            print("No trades")
            return
        
        ttl_trades = len(self.df_results)

        # 1|-1
        wins = self.df_results[self.df_results['result'] == 1]
        losses = self.df_results[self.df_results['result'] == -1]

        total_wins = len(wins)
        total_losses = len(losses)

        if ttl_trades > 0:
            win_rate = (total_wins / ttl_trades) * 100
        else:
            win_rate = 0.0

        # have to add more stats using pnls
        total_pnl = self.df_results['pnl'].sum()

        self.stats = {
            "Total trades": ttl_trades,
            "Total wins": total_wins,
            "Total losses": total_losses,
            "Win rate (%)": win_rate,
            "Total pnl": total_pnl
        }

        for k,v in self.stats.items():
            print(f"{k}: {v}")

        
        


