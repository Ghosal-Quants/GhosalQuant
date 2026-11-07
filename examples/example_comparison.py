from pathlib import Path

import yfinance as yf
import pandas as pd
from matplotlib import pyplot as plt

from example_strategy import ExampleStrategy
from ghosalquant.bench import BenchmarkBuyAndHold
from ghosalquant.bench import BenchmarkTrendVolTarget
from ghosalquant.backtester import DataFrameBacktester
from ghosalquant.util.visualisation import plot_equity_curve

CONFIG = Path(__file__).with_name("config.yaml")

if __name__ == "__main__":
    df: pd.DataFrame | None = yf.download(
        "AAPL",
        start="2024-01-01",
        end="2025-12-31",
        auto_adjust=True,
        progress=False
    )
    if df is None or df.empty:
        raise ValueError("Unable to download data from YFinance")

    example_strategy = ExampleStrategy()
    bnh_strategy = BenchmarkBuyAndHold()
    trend_strategy = BenchmarkTrendVolTarget()

    # Run backtests
    example_bt = DataFrameBacktester(
        strategy=example_strategy,
        dataframe=df,
        config_path=CONFIG,
        symbol="AAPL",
    )
    example_kpms = example_bt.run()

    bnh_bt = DataFrameBacktester(
        strategy=bnh_strategy,
        dataframe=df,
        config_path=CONFIG,
        symbol="AAPL",
    )
    bnh_kpms = bnh_bt.run()

    trend_bt = DataFrameBacktester(
        strategy=trend_strategy,
        dataframe=df,
        config_path=CONFIG,
        symbol="AAPL",
    )
    trend_kpms = trend_bt.run()

    print("\n=== KPMs ===")
    print("\n[ExampleStrategy]\n", example_kpms)
    print("\n[BenchmarkBuyAndHold]\n", bnh_kpms)
    print("\n[BenchmarkTrendVolTarget]\n", trend_kpms)

    ax = plot_equity_curve(
        example_bt.equity_curve,
        title="Equity Curve Comparison (AAPL)",
        label="ExampleStrategy",
        show_realised_unrealised=False,
    )
    plot_equity_curve(
        bnh_bt.equity_curve,
        title="Equity Curve Comparison (AAPL)",
        label="Benchmark Buy & Hold",
        ax=ax,
        show_realised_unrealised=False,
    )
    plot_equity_curve(
        trend_bt.equity_curve,
        title="Equity Curve Comparison (AAPL)",
        label="Benchmark Trend Following",
        ax=ax,
        show_realised_unrealised=False,
    )

    plt.show()
