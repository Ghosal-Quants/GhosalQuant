from dataclasses import dataclass

from ..core.types import OpenOrderView, SignalType
from ..risk.types import RiskContext


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """
    What a strategy is allowed to know about its own state when it receives an order book or a
    public trade.

    A bar strategy that emits one ``Signal`` per bar can pretend it is stateless. A strategy that
    posts resting orders cannot: whether to bid depends on whether a bid is already resting. The
    ``ExecutionSystem`` builds one context per event from the ``Portfolio`` and, when the broker
    implements ``OpenOrderQueries``, the ``Broker``.

    ``open_orders`` holds this symbol's resting orders only. ``risk`` is the same ``RiskContext``
    the ``RiskManager`` will evaluate the resulting signals against.
    """
    symbol: str
    position_quantity: float
    average_entry_price: float | None
    cash: float
    open_orders: tuple[OpenOrderView, ...]
    risk: RiskContext

    @property
    def has_open_orders(self) -> bool:
        return len(self.open_orders) > 0

    @property
    def open_buy_quantity(self) -> float:
        return float(sum(view.remaining_quantity for view in self.open_orders if view.side is SignalType.BUY))

    @property
    def open_sell_quantity(self) -> float:
        return float(sum(view.remaining_quantity for view in self.open_orders if view.side is SignalType.SELL))

    def open_orders_for(self, side: SignalType) -> tuple[OpenOrderView, ...]:
        return tuple(view for view in self.open_orders if view.side is side)

    def open_quantity_at(self, price: float, side: SignalType, *, tolerance: float = 1e-9) -> float:
        """Remaining quantity of this symbol's resting orders on ``side`` at ``price``."""
        total = 0.0
        for view in self.open_orders:
            if view.side is not side:
                continue
            resting_price = view.limit_price if view.limit_price is not None else view.order.limit_price
            if abs(float(resting_price) - float(price)) <= tolerance:
                total += float(view.remaining_quantity)
        return total
