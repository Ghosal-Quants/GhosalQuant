import yfinance as yf
import pandas as pd

from example_strategy import ExampleStrategy
from ghosalquant.backtester.dataframe_backtester import DataFrameBacktester


if __name__ == "__main__":
    print("Example Implementation With RiskManagerBacktester: ")

    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()

    # Download OHLCV data
    df: pd.DataFrame = yf.download("AAPL", start="2020-01-01", end="2020-01-31", auto_adjust=True, progress=False)

    backtester = DataFrameBacktester(
        strategy=strategy,
        dataframe=df,
        config_path="config.yaml"
    )

    # Run backtester
    df2: pd.DataFrame = backtester.run()
    print(df2)


