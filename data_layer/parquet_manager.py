import os
import pandas as pd

class ParquetManager:
    """
    Handles saving and loading data in Parquet format.
    """

    def __init__(self, base_path="data_store"):
        self.base_path = base_path
        os.makedirs(self.base_path, exist_ok=True)

    def save(self, df: pd.DataFrame, ticker: str):
        if df is None or df.empty:
            print(f"⚠️ No data to save for {ticker}")
            return
        path = os.path.join(self.base_path, f"{ticker}.parquet")
        df.to_parquet(path, index=False)
        print(f"✅ Saved {len(df)} rows for {ticker} to {path}")

    def load(self, ticker: str) -> pd.DataFrame | None:
        path = os.path.join(self.base_path, f"{ticker}.parquet")
        if not os.path.exists(path):
            print(f"🚫 No data found for {ticker}")
            return None
        df = pd.read_parquet(path)
        print(f"📦 Loaded {len(df)} rows for {ticker}")
        return df
