import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
class PlotManager():
    def __init__(self):
        pass
    
    def realizedGainsPlot(self,df_results):
        """
            Plot cumulative realized gains where:
            - x axis: date of closure (`timestamp_exit` from `df_results`)
            - y axis: cumulative realized gains (sum of realized gains up to that date)

            The per-trade realized gain is computed as:
            - for long trades (`SIGNAL == 1`): exit_price - entry_price
            - for short trades (`SIGNAL == -1`): entry_price - exit_price
        """

        if df_results is None or df_results.empty:
            print("No results to plot")
            return

        df = df_results.copy()

        # ensure required columns exist
        if not {'entry_price', 'exit_price', 'SIGNAL', 'exit_time'}.issubset(df.columns):
            print("df_results missing required columns: need 'entry_price','exit_price','SIGNAL','timestamp_exit'")
            return

        # compute realized gain per trade (account for shorts)
        df['realized_gain'] = np.where(
            df['SIGNAL'] == 1,
            df['exit_price'] - df['entry_price'],
            df['entry_price'] - df['exit_price']
        )

        # normalize/convert exit timestamps to datetimes and group by date
        df['timestamp_exit'] = pd.to_datetime(df['exit_time'])
    

        gains_by_date = df.groupby('exit_time')['realized_gain'].sum().sort_index()

        # cumulative realized gains over time
        cum_gains = gains_by_date.cumsum()

        plt.figure(figsize=(10, 6))
        plt.plot(cum_gains.index, cum_gains.values, marker='o', linestyle='-')
        plt.title('Cumulative Realized Gains Over Time')
        plt.xlabel('Exit Date')
        plt.ylabel('Cumulative Realized Gains')
        plt.axhline(0, color='red', linestyle='--')
        plt.grid()
        plt.xticks(rotation=45)
        plt.tight_layout()
        plt.legend()
        #plt.show()
    
    def draw(self):
        plt.show()
"""        
fig1 = plt.figure()
plt.plot(range(10), label="Figure 1")
plt.title("First Plot")
plt.legend()

# Second figure
fig2 = plt.figure()
plt.plot(range(10, 20), label="Figure 2")
plt.title("Second Plot")

"""
