from datetime import datetime
from typing import Any, Iterable
import math

SECONDS_PER_YEAR = 365.25 * 24 * 60 * 60


def risk_adjusted_metrics(
    equity_curve: Any,
    *,
    init_capital: float = 0.0,
    risk_free_rate: float = 0.0,
    timestamp_column: str = "time_stamp",
    equity_column: str = "equity",
) -> dict[str, float | int | None]:
    """
    Risk-adjusted metrics for an equity curve.

    Accepts an iterable of ``EquityPoint`` (or anything with ``time_stamp`` and ``equity``) or a
    ``pandas.DataFrame`` with the named columns. The curve does not have to be evenly spaced:
    periods per year are inferred from elapsed time, so an event-driven curve annualises
    correctly. ``risk_free_rate`` is annual. Ratios that are not meaningful for the supplied path
    return ``None`` rather than an artificial infinity.
    """
    stamps, equity = _normalise(
        equity_curve,
        init_capital=init_capital,
        timestamp_column=timestamp_column,
        equity_column=equity_column,
    )

    returns: list[float] = []
    for previous, current in zip(equity, equity[1:]):
        if previous == 0.0:
            continue
        change = current / previous - 1.0
        if math.isfinite(change):
            returns.append(change)

    elapsed_seconds = _elapsed_seconds(stamps)
    elapsed_years = elapsed_seconds / SECONDS_PER_YEAR if elapsed_seconds else None
    elapsed_days = elapsed_seconds / 86_400 if elapsed_seconds else 0.0
    periods_per_year = len(returns) / elapsed_years if elapsed_years and returns else None

    start_equity = equity[0] if equity else float(init_capital)
    end_equity = equity[-1] if equity else float(init_capital)
    annualized_return = _annualized_return(start_equity, end_equity, elapsed_years)
    drawdown = max_drawdown(equity)

    sharpe = sortino = return_volatility = downside_volatility = None
    if periods_per_year and periods_per_year > 0.0 and returns:
        rf = _per_period_risk_free_rate(risk_free_rate, periods_per_year)
        excess = [value - rf for value in returns]
        mean_excess = sum(excess) / len(excess)
        scale = math.sqrt(periods_per_year)

        deviation = _sample_std(returns)
        if deviation > 0.0:
            return_volatility = deviation * scale
            sharpe = mean_excess / deviation * scale

        downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in excess) / len(excess))
        if downside > 0.0:
            downside_volatility = downside * scale
            sortino = mean_excess / downside * scale

    calmar = None
    if annualized_return is not None and drawdown < 0.0:
        calmar = annualized_return / abs(drawdown)

    return {
        "elapsed_days": elapsed_days,
        "return_observations": len(returns),
        "periods_per_year": periods_per_year,
        "annualized_return_pct": None if annualized_return is None else annualized_return * 100.0,
        "return_volatility_pct": None if return_volatility is None else return_volatility * 100.0,
        "downside_volatility_pct": None if downside_volatility is None else downside_volatility * 100.0,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown": drawdown,
    }


def max_drawdown(equity: Iterable[float]) -> float:
    """Worst peak-to-trough fall as a negative fraction (``-0.2`` is 20% below the peak)."""
    peak: float | None = None
    worst = 0.0
    for raw in equity:
        value = float(raw)
        if not math.isfinite(value):
            continue
        peak = value if peak is None else max(peak, value)
        if peak > 0.0:
            worst = min(worst, (value / peak) - 1.0)
    return worst


def format_metric(value: object, fmt: str = ".2f") -> str:
    """Format a metric for display, rendering ``None`` and non-finite values as ``n/a``."""
    if value is None:
        return "n/a"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(numeric):
        return "n/a"
    return format(numeric, fmt)


def _normalise(
    equity_curve: Any,
    *,
    init_capital: float,
    timestamp_column: str,
    equity_column: str,
) -> tuple[list[datetime | None], list[float]]:
    if equity_curve is None:
        return [], []

    if hasattr(equity_curve, "columns"):
        return _normalise_frame(equity_curve, timestamp_column=timestamp_column, equity_column=equity_column)

    stamps: list[datetime | None] = []
    equity: list[float] = []
    for point in equity_curve:
        value = getattr(point, equity_column, None)
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(numeric):
            continue
        stamps.append(getattr(point, timestamp_column, None))
        equity.append(numeric)
    return stamps, equity


def _normalise_frame(frame: Any, *, timestamp_column: str, equity_column: str):
    import pandas as pd

    if frame.empty or equity_column not in frame:
        return [], []
    curve = frame[[column for column in (timestamp_column, equity_column) if column in frame]].copy()
    curve[equity_column] = pd.to_numeric(curve[equity_column], errors="coerce")
    curve = curve.dropna(subset=[equity_column])
    if timestamp_column in curve:
        curve[timestamp_column] = pd.to_datetime(curve[timestamp_column], utc=True, errors="coerce")
        curve = curve.sort_values(timestamp_column, kind="stable")
    if curve.empty:
        return [], []
    stamps = list(curve[timestamp_column]) if timestamp_column in curve else [None] * len(curve)
    return stamps, [float(value) for value in curve[equity_column]]


def _elapsed_seconds(stamps: list[datetime | None]) -> float | None:
    present = [stamp for stamp in stamps if stamp is not None and not _is_nat(stamp)]
    if len(present) < 2:
        return None
    seconds = (present[-1] - present[0]).total_seconds()
    return seconds if math.isfinite(seconds) and seconds > 0.0 else None


def _is_nat(value: Any) -> bool:
    return value != value


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    deviation = math.sqrt(variance)
    return deviation if math.isfinite(deviation) else 0.0


def _annualized_return(start_equity: float, end_equity: float, elapsed_years: float | None) -> float | None:
    if not elapsed_years or elapsed_years <= 0.0:
        return None
    if start_equity <= 0.0 or end_equity <= 0.0:
        return None
    annual_log_return = math.log(end_equity / start_equity) / elapsed_years
    if annual_log_return > 700.0:
        return None
    if annual_log_return < -700.0:
        return -1.0
    return math.exp(annual_log_return) - 1.0


def _per_period_risk_free_rate(risk_free_rate: float, periods_per_year: float) -> float:
    if periods_per_year <= 0.0 or risk_free_rate <= -1.0:
        return 0.0
    return (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
