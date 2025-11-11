import os
import requests
import pandas as pd
from dotenv import load_dotenv

class PolygonSource:
    """
    Fetches market data directly from the Polygon.io REST API.
    """

    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("POLYGON_API_KEY")
        if not self.api_key:
            raise ValueError("❌ Missing POLYGON_API_KEY in .env file.")
        self.base_url = "https://api.polygon.io/v2"

    def fetch_data(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """
        Fetch daily OHLCV data for a given ticker and date range.
        Example: fetch_data('AAPL', '2024-01-01', '2024-12-31')
        """
        url = f"{self.base_url}/aggs/ticker/{ticker}/range/1/day/{start}/{end}"
        params = {"apiKey": self.api_key}
        print(f"🔍 Fetching {ticker}...")

        r = requests.get(url, params=params)

        if r.status_code != 200:
            print(f"❌ Error {r.status_code}: {r.text[:120]}")
            return pd.DataFrame()

        data = r.json().get("results", [])
        if not data:
            print(f"⚠️ No data for {ticker}")
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df["Date"] = pd.to_datetime(df["t"], unit="ms")
        df.rename(
            columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"},
            inplace=True,
        )
        return df[["Date", "Open", "High", "Low", "Close", "Volume"]]
