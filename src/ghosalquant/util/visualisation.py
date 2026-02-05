from typing import Iterable
import matplotlib.pyplot as plt

from ..core.types import EquityPoint

def plot_equity_curve(
        equity_curve: Iterable[EquityPoint],
        *,
        title: str = "Equity Curve",
        label: str = "Equity",
        show_realised_unrealised: bool = False,
        ax: plt.Axes | None = None
) -> plt.Axes:
    points = list(equity_curve)
    if not points:
        raise ValueError("Equity curve is empty.")

    ts = [p.time_stamp for p in points]
    eq = [float(p.equity) for p in points]

    if ax is None:
        figure, ax = plt.subplots()

    ax.plot(ts, eq, label=label)
    ax.set_title(title)
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity")
    ax.grid(True)
    ax.legend()

    if show_realised_unrealised: # Plot realised and unrealised PnL separately
        figure, pnl_ax = plt.subplots()
        realised = [float(p.realised_pnl) for p in points]
        unrealised = [float(p.unrealised_pnl) for p in points]

        pnl_ax.plot(ts, realised, label="Realised PnL")
        pnl_ax.plot(ts, unrealised, label="Unrealised PnL")
        pnl_ax.set_title(f"{title} - Realised and Unrealised PnL")
        pnl_ax.set_xlabel("Time")
        pnl_ax.set_ylabel("PnL")
        pnl_ax.grid(True)
        pnl_ax.legend()

    return ax
