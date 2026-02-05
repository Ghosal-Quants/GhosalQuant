import yfinance as yf
import pandas as pd
from matplotlib import pyplot as plt

from example_strategy import ExampleStrategy
from ghosalquant.backtester.dataframe_backtester import DataFrameBacktester
from ghosalquant.util.visualisation import plot_equity_curve

if __name__ == "__main__":
    print("Example Implementation With DataFrameBacktester: ")

    # strategy receives bars and produces directional intent signals (BUY / SELL)
    strategy = ExampleStrategy()

    # Download OHLCV data
    df: pd.DataFrame = yf.download("AAPL", start="2024-01-01", end="2025-12-31", auto_adjust=True, progress=False)

    backtester = DataFrameBacktester(
        strategy=strategy,
        dataframe=df,
        config_path="config.yaml"
    )

    # Run backtester and get key performance metrics
    kpms = backtester.run()
    df2: pd.DataFrame = backtester.dataframe

    print("KEY PERFORMANCE METRICS:\n" + str(kpms) + "\n")

    print("REALISED PNL: ", backtester.realised_pnl)
    plot_equity_curve(backtester.equity_curve, show_realised_unrealised=True)
    plt.show()
