import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from matplotlib.widgets import CheckButtons
import plotly.graph_objects as go


class realizedGainsPlot:
    def __init__(self, df_results):
        self.df_results = df_results
        
    def draw(self):
        """
        Plot cumulative realized gains where:
        - x axis: date of closure (`timestamp_exit` from `df_results`)
        - y axis: cumulative realized gains (sum of realized gains up to that date)

        The per-trade realized gain is computed as:
        - for long trades (`SIGNAL == 1`): exit_price - entry_price
        - for short trades (`SIGNAL == -1`): entry_price - exit_price
        """

        if self.df_results is None or self.df_results.empty:
            print("No results to plot")
            return

        df = self.df_results.copy()

        # ensure required columns exist
        if not {'entry_price', 'exit_price', 'SIGNAL', 'timestamp_exit'}.issubset(df.columns):
            print("df_results missing required columns: need 'entry_price','exit_price','SIGNAL','timestamp_exit'")
            return

        # compute realized gain per trade (account for shorts)
        df['realized_gain'] = np.where(
            df['SIGNAL'] == 1,
            df['exit_price'] - df['entry_price'],
            df['entry_price'] - df['exit_price']
        )

        # normalize/convert exit timestamps to datetimes and group by date
        df['timestamp_exit'] = pd.to_datetime(df['timestamp_exit'])
        df['exit_date'] = df['timestamp_exit'].dt.date

        gains_by_date = df.groupby('exit_date')['realized_gain'].sum().sort_index()

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
        plt.show()

class assetPricePlot:
    def __init__(self, assetDFList, assetNames, df_results=None):
        self.assetDFList = assetDFList
        self.assetNames = assetNames
        self.df_results = df_results

    def draw(self, enabled_assets=None):
        """
        Draw asset price lines with interactive check buttons to toggle visibility.

        - `enabled_assets`: optional iterable of asset names (or indices) that should start enabled.
        """

        if not self.assetDFList or not self.assetNames:
            print("No asset data to plot")
            return

        # prepare figure and axis
        fig, ax = plt.subplots(figsize=(12, 6))

        lines = []
        labels = []

        for i, df in enumerate(self.assetDFList):
            # try to parse Date column to datetime; if index is already datetime use that
            if 'Date' in df.columns:
                x = pd.to_datetime(df['Date'])
            else:
                try:
                    x = pd.to_datetime(df.index)
                except Exception:
                    x = range(len(df))

            y = df['Close']
            label = self.assetNames[i] if i < len(self.assetNames) else f'asset_{i}'
            (line,) = ax.plot(x, y, label=label)
            lines.append(line)
            labels.append(label)

        ax.set_title('Asset Prices Over Time')
        ax.set_xlabel('Date')
        ax.set_ylabel('Close Price')
        ax.grid(True)
        plt.xticks(rotation=45)

        # place check buttons on the right side
        # width,height in fraction of figure
        rax = plt.axes([0.92, 0.4, 0.07, 0.2])

        # determine initial visibility
        if enabled_assets is None:
            visibility = [line.get_visible() for line in lines]
        else:
            visibility = []
            for i, lab in enumerate(labels):
                if lab in enabled_assets or (isinstance(enabled_assets, (list, tuple)) and i in enabled_assets):
                    visibility.append(True)
                    lines[i].set_visible(True)
                else:
                    visibility.append(False)
                    lines[i].set_visible(False)

        check = CheckButtons(rax, labels, visibility)

        def _toggle(label):
            idx = labels.index(label)
            vis = not lines[idx].get_visible()
            lines[idx].set_visible(vis)
            plt.draw()

        check.on_clicked(_toggle)

        # show legend updated for visible lines
        # create legend entries that reflect visibility
        legend = ax.legend(loc='upper left')

        plt.tight_layout(rect=[0, 0, 0.9, 1])
        plt.show()

class unrealizedGainsPlot:
    def __init__(self, df_results,assetDFList):
        pass 
    def draw(self):
        pass
       #need to run throguh the assetDFList to calculate unrealized gains for each timestep

class candleStickPlot:
    def __init__(self, df, title="Candlestick Chart"):
        fig=go.Figure(data=[go.Candlestick(
        open = df['Open'],
        high = df['High'],
        low = df['Low'],
        close = df['Close'],
        x = df['Date'],
        #dates = pd.to_datetime(df['Date'])
        )])
        fig.update_layout(title=title, xaxis_title='Date', yaxis_title='Price')
        fig.show()
        pass
    def draw(self):
        
        pass


