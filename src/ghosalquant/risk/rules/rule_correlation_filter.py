import math

from ...core.types import SignalType
from ..types import RiskRule, OrderDraft, RiskContext


def _pearson(x: list[float], y: list[float]) -> float:
    """Pearson correlation between two equal-length price series (on returns)."""
    n = min(len(x), len(y))
    if n < 3:
        return 0.0

    # Align to same length (take tail)
    x = x[-n:]
    y = y[-n:]

    # Compute log returns
    rx = [math.log(x[i] / x[i - 1]) if x[i - 1] > 0 else 0.0 for i in range(1, n)]
    ry = [math.log(y[i] / y[i - 1]) if y[i - 1] > 0 else 0.0 for i in range(1, n)]

    m = len(rx)
    if m < 2:
        return 0.0

    mx = sum(rx) / m
    my = sum(ry) / m

    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / m
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx) / m)
    sy = math.sqrt(sum((b - my) ** 2 for b in ry) / m)

    if sx < 1e-12 or sy < 1e-12:
        return 0.0

    return cov / (sx * sy)


class CorrelationFilter(RiskRule):
    def __init__(self, lookback: int = 60, threshold: float = 0.75) -> None:
        """
        Blocks a BUY order when the proposed symbol is too highly correlated with
        any currently-held position.

        Correlation is computed from ``context.price_history`` (a dict mapping
        symbol -> list of recent close prices) using Pearson correlation on
        log-returns over the most recent ``lookback`` bars.

        If ``price_history`` is ``None`` or does not contain the required symbols
        the rule passes through (no block).

        :param lookback: int
            Number of recent price bars to use for the correlation window.
            Default ``60``.
        :param threshold: float
            Absolute correlation level at or above which the order is blocked.
            Default ``0.75``.
        """
        self.lookback = int(lookback)
        self.threshold = float(threshold)

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.side != SignalType.BUY:
            return True

        if context.price_history is None:
            return True

        candidate_prices = context.price_history.get(draft.symbol)
        if candidate_prices is None or len(candidate_prices) < 3:
            return True

        held_symbols = set(context.open_qty_by_symbol.keys())
        if not held_symbols:
            return True

        candidate_window = candidate_prices[-self.lookback:]

        for held_sym in held_symbols:
            held_prices = context.price_history.get(held_sym)
            if held_prices is None or len(held_prices) < 3:
                continue

            held_window = held_prices[-self.lookback:]
            corr = _pearson(candidate_window, held_window)

            if abs(corr) >= self.threshold:
                print(
                    f"[CorrelationFilter] Blocked {draft.symbol}: "
                    f"|corr| with {held_sym} = {abs(corr):.4f} >= {self.threshold}"
                )
                return False

        return True
