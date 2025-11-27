from typing import Callable
import pandas as pd
from trade import Trade

# Define the type of the function being passed in:
# It takes two floats as arguments, and returns a float.
strategy_func = Callable[[pd.DataFrame], pd.DataFrame]


class Backtester:
    def __init__(self, df,asset_Name, strategy: strategy_func):
        self.df = df
        self.strategy = strategy
        print("flag1")
        print(df.head())
        self.assetDataFrames =[df]
        self.assetNames = [asset_Name]
        self.buy_sell_signals = None
        # prepare the data 
        self.prepare_data()

        # apply strategy
        self.apply_strategy()

    def add_df(self, df: pd.DataFrame, asset_Name: str):
        """
        add a dataframe to the list of dataframes 
        """
        
        self.assetDataFrames.append(df)
        self.assetNames.append(asset_Name)
        
        pass

    def prepare_data(self):
        
        for i in range(len(self.assetDataFrames)):
            print(f"Preparing data for asset: {self.assetNames[i]}")
            df=self.assetDataFrames[i]
            #print(f"{df.head()}")
            # ADD BUY/SELL COLUMNs
            #df["BUY"] = 0
            #df["SELL"] = 0
            self.assetDataFrames[i]=df

        pass

    def apply_strategy(self):
        """
        Apply strategy to self.df
        
        Should give back a df with BUY/SELL
        """
        # Build a DataFrame of signals where each row corresponds to a timestep
        # and each column corresponds to an asset (by name).
        if not self.assetDataFrames:
            self.buy_sell_signals = pd.DataFrame()
            return
        ## stsrt
        num_assets = len(self.assetDataFrames)
        num_candles = len(self.assetDataFrames[0])

        signal_rows = []

        for t in range(num_candles):
            # Build a DataFrame containing one candle per asset for this timestep.
            # Columns = asset names, index = candle fields (Open/High/Low/Close/etc.)
            candles = {}
            for f, df in enumerate(self.assetDataFrames):
                if t < len(df):
                    candles[self.assetNames[f]] = df.iloc[t]
                else:
                    # If this asset has fewer rows, fill with NA series matching first df's columns
                    candles[self.assetNames[f]] = pd.Series([pd.NA] * len(self.assetDataFrames[0].columns), index=self.assetDataFrames[0].columns)

            candles_df = pd.DataFrame(candles)

            # Call the user's strategy with the per-asset candle matrix.
            # Strategy signature is flexible here: it may accept a DataFrame and return
            # a dict/Series/list/ndarray/DataFrame of signals indexed/mapped by asset name.
            #try:
                
            result = self.strategy(candles_df)
                
            #except Exception:
                
                #result = None

            # Normalize the result into a list of signals aligned with self.assetNames
            signals = [0] * num_assets

            if result is None:
                # leave as zeros
                pass
            elif isinstance(result, dict):
                for idx, name in enumerate(self.assetNames):
                    signals[idx] = result.get(name, 0)
            elif isinstance(result, pd.Series):
                for idx, name in enumerate(self.assetNames):
                    signals[idx] = result.get(name, 0)
            elif isinstance(result, pd.DataFrame):
                if not result.empty:
                    first_row = result.iloc[0]
                    for idx, name in enumerate(self.assetNames):
                        signals[idx] = first_row.get(name, 0)
            else:
                # Try to treat as iterable (list/ndarray)
                try:
                    for idx, val in enumerate(result):
                        if idx >= num_assets:
                            break
                        signals[idx] = val
                except Exception:
                    # Fallback: scalar -> apply to all assets
                    signals = [result] * num_assets

            signal_rows.append(signals)

        self.buy_sell_signals = pd.DataFrame(signal_rows, columns=self.assetNames)
        pass
        #end

    def run_backtest(self):
        # Ensure signals are up to date
        self.apply_strategy()

        # Initialize tracking containers
        strategy_values = []
        exposure_rows = []
        index_values = []

        # Initialize cash and per-asset unit exposures (number of units held)
        cash = 0.0
        current_Asset_Exposures = {name: 0.0 for name in self.assetNames}

        num_candles = len(self.assetDataFrames[0])

        for t in range(num_candles):
            # Use the index (timestamp) from the first asset if available
            try:
                idx = self.assetDataFrames[0].index[t]
            except Exception:
                idx = t
            index_values.append(idx)

            # Apply signals for this timestep: signals represent change in units (positive buy, negative sell)
            for f in range(len(self.assetDataFrames)):
                signal = self.buy_sell_signals.iloc[t, f]
                asset_name = self.assetNames[f]
                df = self.assetDataFrames[f]

                # determine price for valuation (prefer 'Close')
                if t < len(df):
                    if 'Close' in df.columns:
                        price = df.iloc[t]['Close']
                    
                    else:
                        price = df.iloc[t, df.columns[-1]]
                else:
                    price = 0.0

                # Update positions and cash: signal is treated as units change
                if signal != 0:
                    current_Asset_Exposures[asset_name] += signal
                    cash -= float(signal) * float(price)

            # Compute exposure (market value) per asset at this timestep
            exposure_values = {}
            for f, name in enumerate(self.assetNames):
                df = self.assetDataFrames[f]
                if t < len(df):
                    if 'Close' in df.columns:
                        price = df.iloc[t]['Close']
                    else:
                        price = df.iloc[t, df.columns[-1]]
                else:
                    price = 0.0

                exposure_values[name] = float(current_Asset_Exposures[name]) * float(price)

            exposure_rows.append(exposure_values)

            total_exposure = sum(exposure_values.values())
            strategy_values.append(cash + total_exposure)

        # Store results on the backtester instance for later inspection
        self.asset_exposure = pd.DataFrame(exposure_rows, index=index_values)
        self.strategy_value = pd.Series(strategy_values, index=index_values, name='strategy_value')
        print(self.strategy_value)
        return None
    

    def output_to_excel(self):
        """
        on the tin
        """
        pass
        

