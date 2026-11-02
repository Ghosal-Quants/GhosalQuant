from __future__ import annotations
from typing import Iterable
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
from matplotlib import font_manager as fm

from ..core.types import EquityPoint


# ----- Styling ----- #
_PALETTE = [
    "#1f77b4",  # blue
    "#4c4c4c",  # dark grey
    "#9467bd",  # purple
    "#8c8c8c",  # grey
    "#2ca02c",  # green
]

def _pick_font(candidates: list[str]) -> str:
    available = {f.name for f in fm.fontManager.ttflist}
    for name in candidates:
        if name in available:
            return name
    return "DejaVu Sans"


_FONT = _pick_font(["Segoe UI", "Inter", "Arial", "Helvetica", "DejaVu Sans"])

# ----- Defaults Helpers ----- #
def _default_figsize(fig: plt.Figure) -> None:
    try:
        fig.set_size_inches(12, 6, forward=True)
    except Exception:
        pass


def _apply_concise_date_axis(ax: plt.Axes) -> None:
    try:
        locator = mdates.AutoDateLocator(minticks=6, maxticks=10)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    except Exception:
        pass


def _style_axes(ax: plt.Axes) -> None:
    ax.set_facecolor("#fafafa")

    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    for side in ("left", "bottom"):
        ax.spines[side].set_alpha(0.85)
        ax.spines[side].set_linewidth(1.15)

    ax.grid(axis="y", linestyle="-", alpha=0.12, linewidth=0.8)
    ax.grid(axis="x", visible=False)

    ax.title.set_fontsize(13)
    ax.title.set_fontweight("normal")
    ax.title.set_fontfamily(_FONT)

    ax.xaxis.label.set_size(11)
    ax.yaxis.label.set_size(11)
    ax.xaxis.label.set_fontfamily(_FONT)
    ax.yaxis.label.set_fontfamily(_FONT)

    ax.tick_params(axis="both", labelsize=10, width=1.0, length=3.5)

    for t in ax.get_xticklabels() + ax.get_yticklabels():
        t.set_fontfamily(_FONT)
        t.set_alpha(0.9)

    ax.margins(x=0)


def _style_legend(ax: plt.Axes) -> None:
    leg = ax.get_legend()
    if leg is None:
        return

    leg.set_frame_on(False)
    for text in leg.get_texts():
        text.set_fontsize(10)
        text.set_fontfamily(_FONT)
        text.set_alpha(0.9)


def _maybe_set_palette(ax: plt.Axes, owns_ax: bool) -> None:
    if owns_ax:
        ax.set_prop_cycle(color=_PALETTE)

# ----- Plotting ----- #
def plot_equity_curve(
        equity_curve: Iterable[EquityPoint],
        *,
        title: str = "Equity Curve",
        label: str = "Equity",
        show_realised_unrealised: bool = False,
        ax: plt.Axes | None = None,
) -> plt.Axes:
    points = list(equity_curve)
    if not points:
        raise ValueError("Equity curve is empty.")

    ts = [p.time_stamp for p in points]
    eq = [float(p.equity) for p in points]

    owns_ax = ax is None
    if owns_ax:
        fig, ax = plt.subplots()
        _default_figsize(fig)
    else:
        fig = ax.figure

    _maybe_set_palette(ax, owns_ax)

    ax.plot(ts, eq, label=label, linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel("Time")
    ax.set_ylabel("Equity")

    # axis formatting
    _apply_concise_date_axis(ax)
    ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("{x:,.0f}"))

    _style_axes(ax)

    if label:
        ax.legend(loc="best")
        _style_legend(ax)

    if owns_ax:
        fig.tight_layout()

    if show_realised_unrealised:
        pnl_fig, pnl_ax = plt.subplots()
        _default_figsize(pnl_fig)
        pnl_ax.set_prop_cycle(color=_PALETTE)

        realised = [float(p.realised_pnl) for p in points]
        unrealised = [float(p.unrealised_pnl) for p in points]

        pnl_ax.plot(ts, realised, label="Realised PnL", linewidth=1.1)
        pnl_ax.plot(ts, unrealised, label="Unrealised PnL", linewidth=1.1)

        pnl_ax.axhline(0.0, linewidth=0.8, alpha=0.25)

        pnl_ax.set_title(f"{title} - Realised vs Unrealised")
        pnl_ax.set_xlabel("Time")
        pnl_ax.set_ylabel("PnL")

        _apply_concise_date_axis(pnl_ax)
        pnl_ax.yaxis.set_major_formatter(mticker.StrMethodFormatter("{x:,.0f}"))

        _style_axes(pnl_ax)
        pnl_ax.legend(loc="best")
        _style_legend(pnl_ax)
        pnl_fig.tight_layout()

    return ax