import pandas as pd
from datetime import datetime
from pathlib import Path

from core.types import BacktestKPMs, SignalType
from util.deprecation import deprecated

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def output_to_excel(df_results: pd.DataFrame) -> None:
    """
    Outputs a given dataframe to an Excel file in `results/trades`
    """

    if df_results is None:
        raise ValueError("Expected df_results to not be None.")

    out_dir = PROJECT_ROOT / "results" / "trades"
    out_dir.mkdir(parents=True, exist_ok=True)

    # save in results/trades
    filename = f"backtest_results_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx"
    filepath = out_dir / filename

    if df_results.empty:
        print("No closed trades to save.")
        return

    df_results.to_excel(filepath, index=False)
    print(f"Saved results to {filepath}.")


@deprecated("Legacy code. Should probably be handled with BacktestKPMs methods directly.")
def calculate_stats(df_results: pd.DataFrame) -> BacktestKPMs:
    """
    Calculates key performance metrics
    """

    if df_results is None:
        print("No trades")
        return BacktestKPMs(total_trades=0, total_wins=0, total_losses=0, win_rate_pct=0.0, total_pnl=0.0)

    ttl_trades = len(df_results)

    # 1|-1
    wins = df_results[df_results['result'] == 1]
    losses = df_results[df_results['result'] == -1]

    total_wins = len(wins)
    total_losses = len(losses)

    if ttl_trades > 0:
        win_rate = (total_wins / ttl_trades) * 100
    else:
        win_rate = 0.0

    # have to add more stats using pnls
    total_pnl = df_results['pnl'].sum()

    stats = BacktestKPMs(
        total_trades= ttl_trades,
        total_wins= total_wins,
        total_losses= total_losses,
        win_rate_pct= win_rate,
        total_pnl= total_pnl
    )

    print(str(stats))
    return stats
