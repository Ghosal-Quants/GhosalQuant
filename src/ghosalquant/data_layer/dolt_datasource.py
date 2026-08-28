import csv
import io
import re
import subprocess
from datetime import date, datetime, time
from pathlib import Path
from typing import AsyncIterable, Iterable

from ..core.contracts import Contract, ContractRegistry, Right
from ..core.types import Bar, BookLevel, MarketEvent, OrderBook
from .datasource_base import DataSource

# One snapshot a session.
SNAPSHOT = time(16, 0)
_SYMBOL = re.compile(r"^[A-Z0-9.\-]{1,16}$")


class DoltOptionsDataSource(DataSource):
    """
    Real end-of-day bid and ask from ``post-no-preference/options`` on DoltHub. ``dolt clone``
    it (8 GB), point this at the directory, and have ``dolt`` on PATH.

    Samples about three sessions a week, so consecutive days cannot be assumed. The table has
    no spot, so the underlier is the chain's own forward by put-call parity.
    """

    def __init__(
        self,
        database: str | Path,
        *,
        symbols: Iterable[str],
        start: date | None = None,
        end: date | None = None,
        max_tenor_days: int | None = None,
        strike_band: float | None = None,
        include_underlier: bool = True,
        multiplier: float = 100.0,
        dolt: str = "dolt",
    ) -> None:
        self.database = Path(database)
        self.symbols = [s.strip().upper() for s in symbols if s and s.strip()]
        if not self.symbols:
            raise ValueError("At least one symbol must be provided.")
        for symbol in self.symbols:
            if not _SYMBOL.match(symbol):
                raise ValueError(f"Not a ticker: {symbol!r}")
        self.start, self.end = start, end
        self.max_tenor_days = max_tenor_days
        self.strike_band = strike_band
        self.include_underlier = include_underlier
        self.multiplier = multiplier
        self.dolt = dolt
        self._contracts = ContractRegistry()
        self._events: list[MarketEvent] | None = None

    @property
    def contracts(self) -> ContractRegistry:
        if self._events is None:
            self._events = self._load()
        return self._contracts

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        if self._events is None:
            self._events = self._load()
        for event in self._events:
            yield event

    def query(self) -> str:
        names = ", ".join(f"'{s}'" for s in self.symbols)
        where = [f"act_symbol in ({names})", "bid > 0", "ask > bid"]
        if self.start:
            where.append(f"`date` >= '{self.start:%Y-%m-%d}'")
        if self.end:
            where.append(f"`date` <= '{self.end:%Y-%m-%d}'")
        if self.max_tenor_days is not None:
            where.append(f"datediff(expiration, `date`) <= {int(self.max_tenor_days)}")
        return (
            "select `date`, act_symbol, expiration, strike, call_put, bid, ask, vol, delta "
            "from option_chain where " + " and ".join(where) + " order by `date`, expiration, strike"
        )

    def _rows(self) -> list[dict]:
        result = subprocess.run(
            [self.dolt, "sql", "-q", self.query(), "-r", "csv"],
            cwd=self.database, capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"dolt query failed: {result.stderr.strip()[:300]}")
        return list(csv.DictReader(io.StringIO(result.stdout)))

    def _load(self) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        chains: dict[tuple, dict] = {}

        for row in self._rows():
            at = datetime.combine(date.fromisoformat(row["date"]), SNAPSHOT)
            expiry = date.fromisoformat(row["expiration"])
            strike, bid, ask = float(row["strike"]), float(row["bid"]), float(row["ask"])
            right = Right.CALL if row["call_put"].strip().lower().startswith("c") else Right.PUT
            underlier = row["act_symbol"].strip().upper()

            symbol = self.option_symbol(underlier, expiry, right, strike)
            if symbol not in self._contracts:
                self._contracts.register(Contract(
                    symbol=symbol, underlier=underlier, multiplier=self.multiplier,
                    right=right, strike=strike, expiry=expiry,
                ))
            events.append(OrderBook(
                time_stamp=at, symbol=symbol,
                bids=(BookLevel(bid, 0.0),), asks=(BookLevel(ask, 0.0),),
            ))
            chains.setdefault((at, underlier, expiry), {}).setdefault(strike, {})[right] = (
                (bid + ask) / 2.0
            )

        spot = self._forwards(chains)
        if self.include_underlier:
            events.extend(
                Bar(time_stamp=at, symbol=underlier, open=price, high=price, low=price,
                    close=price, volume=0.0)
                for (at, underlier), price in spot.items()
            )
        if self.strike_band is not None:
            events = self._near_the_money(events, spot)

        underliers = set(self.symbols)
        events.sort(key=lambda e: (e.time_stamp, e.symbol not in underliers, e.symbol))
        return events

    def option_symbol(self, underlier: str, expiry: date, right: Right, strike: float) -> str:
        letter = "C" if right is Right.CALL else "P"
        return f"{underlier:<6}{expiry:%y%m%d}{letter}{round(strike * 1000):08d}"

    @staticmethod
    def _forwards(chains: dict) -> dict[tuple, float]:
        """The underlier per session, from the nearest expiry's closest call/put pair."""
        best: dict[tuple, tuple] = {}
        for (at, underlier, expiry), by_strike in chains.items():
            pairs = [(abs(sides[Right.CALL] - sides[Right.PUT]), strike, sides)
                     for strike, sides in by_strike.items()
                     if Right.CALL in sides and Right.PUT in sides]
            if not pairs:
                continue
            _, strike, sides = min(pairs, key=lambda pair: (pair[0], pair[1]))
            forward = strike + sides[Right.CALL] - sides[Right.PUT]
            key = (at, underlier)
            if key not in best or expiry < best[key][0]:
                best[key] = (expiry, forward)
        return {key: forward for key, (_, forward) in best.items() if forward > 0.0}

    def _near_the_money(self, events: list[MarketEvent], spot: dict) -> list[MarketEvent]:
        kept = []
        for event in events:
            contract = self._contracts.get(event.symbol)
            reference = spot.get((event.time_stamp, contract.underlier))
            if (contract.strike is None or reference is None
                    or abs(contract.strike - reference) <= self.strike_band):
                kept.append(event)
        return kept
