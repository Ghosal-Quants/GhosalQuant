from datetime import datetime
from typing import Protocol, Iterable

from core.types import Trade, EquityPoint, Signal


class Portfolio(Protocol):
    # TODO: Eventually make this use a new class like Fills instead of Signals for cleaner separation of concerns
    async def process_signal(self, signal: Signal) -> None:
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
