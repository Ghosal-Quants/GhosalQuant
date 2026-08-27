import pandas as pd
from datetime import datetime
from pathlib import Path

from ..core.types import BacktestKPMs, SignalType
from ..metrics.risk_adjusted import risk_adjusted_metrics
from ..portfolio.portfolio_base import Portfolio
from ..util.deprecation import deprecated

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
    filename = f"backtest_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = out_dir / filename

    if df_results.empty:
        print("No closed trades to save.")
        return

    df_results.to_excel(filepath, index=False)
    print(f"Saved results to {filepath}.")


@deprecated("Legacy code. Should probably be handled with BacktestKPMs methods directly. Only works with legacy backtester and strategy code.")
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


def calculate_kpms(portfolio: Portfolio, *, risk_free_rate: float = 0.0) -> BacktestKPMs:
    trades = list(portfolio.closed_trades)
    total_trades = len(trades)

    wins = 0
    losses = 0
    for t in trades:
        if (t.realised_pnl or 0.0) > 0.0: # Default to 0.0 if None
            wins += 1
        elif (t.realised_pnl or 0.0) < 0.0:
            losses += 1

    if total_trades > 0:
        win_rate = (wins / total_trades) * 100.0
    else:
        win_rate = 0.0

    curve = list(portfolio.equity_curve)
    risk = risk_adjusted_metrics(
        curve,
        init_capital=curve[0].equity if curve else 0.0,
        risk_free_rate=risk_free_rate,
    )

    return BacktestKPMs(
        total_trades=total_trades,
        total_wins=wins,
        total_losses=losses,
        win_rate_pct=win_rate,
        realised_pnl=portfolio.realised_pnl,
        unrealised_pnl=portfolio.unrealised_pnl,
        total_pnl=portfolio.total_pnl,
        max_drawdown=risk["max_drawdown"],
        annualized_return_pct=risk["annualized_return_pct"],
        return_volatility_pct=risk["return_volatility_pct"],
        downside_volatility_pct=risk["downside_volatility_pct"],
        sharpe_ratio=risk["sharpe_ratio"],
        sortino_ratio=risk["sortino_ratio"],
        calmar_ratio=risk["calmar_ratio"],
    )
