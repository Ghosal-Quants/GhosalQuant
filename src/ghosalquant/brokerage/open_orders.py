from typing import Iterable, Protocol, runtime_checkable

from ..core.types import OpenOrderView, SignalType


@runtime_checkable
class OpenOrderQueries(Protocol):
    """
    Optional broker capability: report and cancel the orders it is currently working.

    ``ExecutionSystem`` checks for it with ``isinstance`` and, when present, fills
    ``StrategyContext.open_orders`` and ``RiskContext.open_orders`` from it. A broker that cannot
    report resting orders simply does not implement it and both stay empty.
    """

    def open_order_views(self, symbol: str | None = None) -> tuple[OpenOrderView, ...]:
        """Resting orders, optionally for one symbol, as read-only views."""
        ...

    def cancel_open_orders(
        self,
        symbol: str,
        side: SignalType | None = None,
        order_slots: Iterable[str] | None = None,
    ) -> int:
        """Cancel this symbol's resting orders (optionally one side or some slots). Returns how many."""
        ...
