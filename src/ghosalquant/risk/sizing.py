from .types import RiskContext


def current_equity(context: RiskContext) -> float | None:
    """Equity now: from the context when it carries it, else rebuilt from peak and drawdown."""
    if context.current_equity is not None:
        return float(context.current_equity)
    if context.peak_equity is None:
        return None
    drawdown = 0.0 if context.current_drawdown is None else float(context.current_drawdown)
    return float(context.peak_equity) * (1.0 + drawdown)


def sizing_capital(
    context: RiskContext, *, capital: float, reference_capital: float | None = None
) -> float:
    """
    The capital a sizing rule should work from.

    Fixed at ``capital``, unless ``reference_capital`` is given: then it scales with how far equity
    has moved from that reference, so a book down 20% sizes 20% smaller. Sizing off a fixed number
    through a drawdown means each order is quietly a larger share of what is left.
    """
    if capital <= 0.0:
        raise ValueError("capital must be > 0")
    if reference_capital is None:
        return capital
    if reference_capital <= 0.0:
        raise ValueError("reference_capital must be > 0")
    equity = current_equity(context)
    return capital if equity is None else capital * max(1.0, equity) / reference_capital


def kelly_fraction(win_probability: float, gain: float, loss: float) -> float:
    """
    The growth-optimal stake for an asymmetric bet: ``(p*b - q*a) / (a*b)``.

    ``gain`` and ``loss`` are fractions of the stake; zero back means no edge.
    """
    p = float(win_probability)
    b, a = float(gain), float(loss)
    if not 0.0 <= p <= 1.0:
        raise ValueError("win_probability must be in [0, 1]")
    if b <= 0.0 or not 0.0 < a <= 1.0:
        raise ValueError("gain must be > 0 and loss in (0, 1]")
    return max(0.0, (p * b - (1.0 - p) * a) / (a * b))
