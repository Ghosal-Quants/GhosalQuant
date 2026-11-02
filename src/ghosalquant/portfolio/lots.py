from bisect import bisect_left
from dataclasses import dataclass
from typing import Iterable

from ..core.types import Trade


@dataclass(frozen=True, slots=True)
class LotLadder:
    """
    The open lots of one symbol, ordered the way they would be closed (LIFO), with prefix sums so
    the acquisition cost of a partial exit is a bisect rather than a walk.
    """
    quantity: float
    cost: float
    prices: tuple[float, ...]
    cumulative_quantity: tuple[float, ...]
    cumulative_cost: tuple[float, ...]

    @classmethod
    def from_trades(cls, trades: Iterable[Trade]) -> "LotLadder":
        lots = sorted(trades, key=lambda trade: (trade.open_time, trade.id), reverse=True)
        prices: list[float] = []
        cumulative_quantity: list[float] = []
        cumulative_cost: list[float] = []
        quantity = 0.0
        cost = 0.0
        for lot in lots:
            size = float(lot.size)
            if size <= 0.0:
                continue
            price = float(lot.entry_price)
            quantity += size
            cost += size * price
            prices.append(price)
            cumulative_quantity.append(quantity)
            cumulative_cost.append(cost)
        return cls(
            quantity=quantity,
            cost=cost,
            prices=tuple(prices),
            cumulative_quantity=tuple(cumulative_quantity),
            cumulative_cost=tuple(cumulative_cost),
        )

    def cost_of(self, quantity: float) -> float:
        """Acquisition cost of the first ``quantity`` units this position would sell."""
        requested = min(max(0.0, float(quantity)), self.quantity)
        if requested <= 0.0 or not self.cumulative_quantity:
            return 0.0
        index = bisect_left(self.cumulative_quantity, requested)
        if index >= len(self.prices):
            return self.cost
        previous_quantity = 0.0 if index == 0 else self.cumulative_quantity[index - 1]
        previous_cost = 0.0 if index == 0 else self.cumulative_cost[index - 1]
        return previous_cost + (requested - previous_quantity) * self.prices[index]


def lot_ladders(trades: Iterable[Trade]) -> dict[str, LotLadder]:
    by_symbol: dict[str, list[Trade]] = {}
    for trade in trades:
        by_symbol.setdefault(trade.symbol, []).append(trade)
    return {symbol: LotLadder.from_trades(lots) for symbol, lots in by_symbol.items()}
