import os
import pandas as pd

class ParquetManager:
    def __init__(self, base_path="data_store"):
        """
        Handles saving and loading data in Parquet format.
        base_path = folder where all parquet files are stored
        """
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)

    def save(self, df, ticker):
        """Save a DataFrame to a Parquet file named after the ticker."""
        path = os.path.join(self.base_path, f"{ticker}.parquet")
        df.to_parquet(path, index=False)
        print(f"✅ Saved {len(df)} rows for {ticker} to {path}")

    def load(self, ticker):
        """Load a Parquet file for a given ticker."""
        path = os.path.join(self.base_path, f"{ticker}.parquet")
        if not os.path.exists(path):
            print(f" No data found for {ticker}")
            return None
        df = pd.read_parquet(path)
        print(f" Loaded {len(df)} rows for {ticker}")
        return df
