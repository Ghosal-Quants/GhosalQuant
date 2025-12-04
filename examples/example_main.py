from datetime import datetime
import yfinance as yf
import pandas as pd

from example_strategy import ExampleStrategy
from data_layer.dataframe_datasource import DataFrameDataSource
from backtester.dataframe_backtester import DataFrameBacktester
from backtester.riskmanager_backtester import RiskManagerBacktester


if __name__ == "__main__":
    print("Example Implementation With RiskManagerBacktester: ")

    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()

    # Download OHLCV data
    df: pd.DataFrame = yf.download("AAPL", start="2020-01-01", end="2020-01-31", auto_adjust=True, progress=False)

    # Initialise backtester with RiskManager
    backtester = RiskManagerBacktester(
        strategy=strategy,
        dataframe=df,
        symbol="AAPL",
    )

    # Run backtester
    df2: pd.DataFrame = backtester.run()
    print(df2)


