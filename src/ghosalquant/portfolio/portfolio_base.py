from datetime import datetime
from typing import Protocol, Iterable

from ..core.types import Trade, EquityPoint, Order


class Portfolio(Protocol):
    async def process_order(self, order: Order) -> None:
        ...

    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        ...

    @property
    def closed_trades(self) -> Iterable[Trade]:
        ...

    @property
    def equity_curve(self) -> Iterable[EquityPoint]:
        ...

    @property
    def realised_pnl(self) -> float:
        ...
