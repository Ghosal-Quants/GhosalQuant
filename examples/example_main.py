from pathlib import Path

import yfinance as yf
import pandas as pd
from matplotlib import pyplot as plt

from example_strategy import ExampleStrategy
from ghosalquant.backtester import Backtester, DataFrameBacktester
from ghosalquant.util.visualisation import plot_equity_curve

CONFIG = Path(__file__).with_name("config.yaml")

if __name__ == "__main__":
    # strategy receives bars and produces directional intent signals (BUY / SELL)
    strategy = ExampleStrategy()

    #"""
    # Download OHLCV data
    df: pd.DataFrame = yf.download("AAPL", start="2024-01-01", end="2025-12-31", auto_adjust=True, progress=False)

    backtester = DataFrameBacktester(
        strategy=strategy,
        dataframe=df,
        config_path=CONFIG,
        symbol="AAPL",
    )
    #"""
    #backtester = Backtester.from_config(strategy=strategy, config_path=CONFIG)

    # Run backtester and get key performance metrics
    kpms = backtester.run()

    print(f"KEY PERFORMANCE METRICS:\n{kpms}\n")

    print("REALISED PNL: ", backtester.realised_pnl)
    plot_equity_curve(backtester.equity_curve, show_realised_unrealised=True)
    plt.show()
