import os
from dotenv import load_dotenv
from data_layer.polygon_source import PolygonSource
from data_layer.parquet_manager import ParquetManager

if __name__ == "__main__":
    load_dotenv()

    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise ValueError("❌ Missing POLYGON_API_KEY in .env file")

    source = PolygonSource()
    parquet_mgr = ParquetManager()

    tickers = ["AAPL", "MSFT", "NVDA"]
    start_date = "2024-01-01"
    end_date = "2024-12-31"

    for ticker in tickers:
        df = source.fetch_data(ticker, start_date, end_date)
        parquet_mgr.save(df, ticker)
