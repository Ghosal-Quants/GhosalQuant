from datetime import datetime
from typing import Protocol, Iterable

from ..core.types import Trade, EquityPoint, Fill, Bar


class Portfolio(Protocol):
    async def process_fill(self, fill: Fill) -> None:
        ...

    def mark(self, symbol: str, price: float, time_stamp: datetime) -> None:
        """
        Record the latest price a position is valued at. Does not snapshot equity; the caller
        decides when an ``EquityPoint`` is worth appending.
        """
        ...

    def update_equity(self, bar: Bar) -> None:
        """``mark(bar.symbol, bar.close, bar.time_stamp)`` followed by an equity snapshot."""
        ...

    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None:
        ...

    def average_entry_price(self, symbol: str) -> float | None:
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
    def cash_balance(self) -> float:
        ...

    @property
    def current_equity(self) -> float:
        ...

    @property
    def peak_equity(self) -> float:
        ...

    def reset_peak_equity(self) -> None:
        ...

    @property
    def open_position_count(self) -> int:
        ...

    @property
    def open_qty_by_symbol(self) -> dict[str, float]:
        ...

    @property
    def open_trades(self) -> Iterable[Trade]:
        """The open lots, in no particular order; valuation orders them itself."""
        ...

    @property
    def last_prices(self) -> dict[str, float]:
        ...

    @property
    def accounting_version(self) -> int:
        """Increments whenever cash or a position quantity changes."""
        ...

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    @property
    def final_balance(self) -> float:
        ...
