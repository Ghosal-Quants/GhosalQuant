import yfinance as yf
import pandas as pd
from matplotlib import pyplot as plt

from example_strategy import ExampleStrategy
from ghosalquant.execution.buy_and_hold_strategy import BenchmarkBnHStrategy
from ghosalquant.backtester.dataframe_backtester import DataFrameBacktester
from ghosalquant.util.visualisation import plot_equity_curve


def run_backtest(strategy, df: pd.DataFrame, config_path: str):
    backtester = DataFrameBacktester(
        strategy=strategy,
        dataframe=df,
        config_path=config_path
    )
    kpms = backtester.run()
    return backtester, kpms

if __name__ == "__main__":
    df: pd.DataFrame = yf.download(
        "AAPL",
        start="2024-01-01",
        end="2025-12-31",
        auto_adjust=True,
        progress=False
    )

    example_strategy = ExampleStrategy()
    benchmark_strategy = BenchmarkBnHStrategy()

    # Run both backtests
    example_bt, example_kpms = run_backtest(example_strategy, df, config_path="config.yaml")
    bench_bt, bench_kpms = run_backtest(benchmark_strategy, df, config_path="config.yaml")

    print("\n=== KPMs ===")
    print("\n[ExampleStrategy]\n", example_kpms)
    print("\n[BenchmarkBuyAndHoldStrategy]\n", bench_kpms)

    print("\n=== Realised PnL ===")
    print("ExampleStrategy:", example_bt.realised_pnl)
    print("BenchmarkBuyAndHoldStrategy:", bench_bt.realised_pnl)

    fig, ax = plt.subplots()
    plot_equity_curve(
        example_bt.equity_curve,
        title="Equity Curve Comparison (AAPL)",
        label="ExampleStrategy",
        ax=ax,
        show_realised_unrealised=False,
    )
    plot_equity_curve(
        bench_bt.equity_curve,
        title="Equity Curve Comparison (AAPL)",
        label="Benchmark Buy & Hold",
        ax=ax,
        show_realised_unrealised=False,
    )

    plt.show()
