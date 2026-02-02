from typing import Iterable
import matplotlib.pyplot as plt

from ..core.types import EquityPoint

def plot_equity_curve(
        equity_curve: Iterable[EquityPoint],
        *,
        title: str = "Equity Curve",
        show_realised_unrealised: bool = False,
) -> None:
    points = list(equity_curve)
    if not points:
        raise ValueError("Equity curve is empty.")

    ts = [p.time_stamp for p in points]
    eq = [float(p.equity) for p in points]

    plt.figure()
    plt.plot(ts, eq, label="Equity")
    plt.title(title)
    plt.xlabel("Time")
    plt.ylabel("Equity")
    plt.grid(True)
    plt.legend()

    if show_realised_unrealised: # Plot realised and unrealised PnL separately
        realised = [float(p.realised_pnl) for p in points]
        unrealised = [float(p.unrealised_pnl) for p in points]

        plt.figure()
        plt.plot(ts, realised, label="Realised PnL")
        plt.plot(ts, unrealised, label="Unrealised PnL")
        plt.title(f"{title} - Realised and Unrealised PnL")
        plt.xlabel("Time")
        plt.ylabel("PnL")
        plt.grid(True)
        plt.legend()

    plt.show()
