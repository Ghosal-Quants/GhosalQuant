from datetime import datetime
from typing import Protocol, Iterable

from ..core.types import Trade, EquityPoint, Fill, Bar


class Portfolio(Protocol):
    async def process_fill(self, fill: Fill) -> None:
        ...

    def update_equity(self, bar: Bar) -> None:
        ...

    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        ...

    def __str__(self) -> str:
        closed = list(self.closed_trades)
        curve = list(self.equity_curve)

        return (
            "Portfolio State:\n"
            f"\tRealised PnL: {self.realised_pnl}\n"
            f"\tUnrealised PnL: {self.unrealised_pnl}\n"
            f"\tTotal PnL: {self.total_pnl}\n"
            f"\tClosed Trades: {len(closed)}\n"
            f"\tEquity Points: {len(curve)}\n"
            f"\tFinal Balance: {self.final_balance}\n"
        )

    @property
    def closed_trades(self) -> Iterable[Trade]:
        ...

    @property
    def equity_curve(self) -> Iterable[EquityPoint]:
        ...

    @property
    def realised_pnl(self) -> float:
        ...

    @property
    def unrealised_pnl(self) -> float:
        ...

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    @property
    def final_balance(self) -> float:
        ...
