import csv
import gzip
import zipfile
from datetime import datetime, time
from pathlib import Path
from typing import AsyncIterable, Iterable

from ..core.contracts import Contract, ContractRegistry, Right
from ..core.types import BookLevel, MarketEvent, OrderBook
from .datasource_base import DataSource

# Cboe stamps its two snapshots at 15:45 and the close, in New York time.
SNAPSHOT_TIMES = {"1545": time(15, 45), "eod": time(16, 0)}


class CboeEODDataSource(DataSource):
    """
    Cboe DataShop "Option EOD Summary" (``UnderlyingOptionsEODCalcs_*``), paid. Real NBBO with
    sizes at 15:45 and at the close, plus the underlier's own quote.

    Prefer ``1545``; the close is distorted by rebalancing. Take the ``cgi_or_historical`` file,
    as the other zeroes every index value. Stamps are New York wall clock with no zone.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        symbols: Iterable[str] | None = None,
        snapshot: str = "1545",
        multiplier: float = 100.0,
        min_open_interest: float = 0.0,
        max_tenor_days: int | None = None,
        strike_band: float | None = None,
        include_underlier: bool = True,
    ) -> None:
        self.path = Path(path)
        self.symbols = None if symbols is None else {s.strip().upper() for s in symbols}
        self.snapshot = snapshot.strip().lower()
        if self.snapshot not in SNAPSHOT_TIMES:
            raise ValueError(f"snapshot must be one of {tuple(SNAPSHOT_TIMES)}")
        self.multiplier = multiplier
        self.min_open_interest = float(min_open_interest)
        self.max_tenor_days = max_tenor_days
        self.strike_band = strike_band
        self.include_underlier = include_underlier
        self._contracts = ContractRegistry()
        self._events: list[MarketEvent] | None = None

    @property
    def contracts(self) -> ContractRegistry:
        """Every option the file described. Empty until it has been read."""
        if self._events is None:
            self._events = self._load()
        return self._contracts

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        if self._events is None:
            self._events = self._load()
        for event in self._events:
            yield event

    def option_symbol(self, root: str, expiry, right: Right, strike: float) -> str:
        letter = "C" if right is Right.CALL else "P"
        return f"{root.strip().upper():<6}{expiry:%y%m%d}{letter}{round(strike * 1000):08d}"

    def _load(self) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        seen_underliers: set[tuple] = set()
        suffix = self.snapshot

        for row in self._rows():
            underlier = row["underlying_symbol"].strip().upper()
            if self.symbols is not None and underlier not in self.symbols:
                continue
            at = datetime.combine(
                datetime.strptime(row["quote_date"], "%Y-%m-%d").date(), SNAPSHOT_TIMES[suffix]
            )

            if self.include_underlier and (underlier, at) not in seen_underliers:
                book = self._book(at, underlier, row.get(f"underlying_bid_{suffix}"),
                                  row.get(f"underlying_ask_{suffix}"), 0.0, 0.0)
                if book is not None:
                    seen_underliers.add((underlier, at))
                    events.append(book)

            option = self._option(row, at, underlier, suffix)
            if option is not None:
                events.append(option)

        underliers = {symbol for symbol, _ in seen_underliers}
        events.sort(key=lambda e: (e.time_stamp, e.symbol not in underliers, e.symbol))
        return events

    def _option(self, row: dict, at: datetime, underlier: str, suffix: str) -> OrderBook | None:
        if float(row.get("open_interest") or 0.0) < self.min_open_interest:
            return None
        expiry = datetime.strptime(row["expiration"], "%Y-%m-%d").date()
        days = (expiry - at.date()).days
        if days < 0 or (self.max_tenor_days is not None and days > self.max_tenor_days):
            return None

        strike = float(row["strike"])
        spot = float(row.get(f"underlying_bid_{suffix}") or 0.0)
        if self.strike_band is not None and spot > 0.0 and abs(strike - spot) > self.strike_band:
            return None

        right = Right.CALL if row["option_type"].strip().upper() == "C" else Right.PUT
        symbol = self.option_symbol(row["root"], expiry, right, strike)
        book = self._book(at, symbol, row.get(f"bid_{suffix}"), row.get(f"ask_{suffix}"),
                          row.get(f"bid_size_{suffix}"), row.get(f"ask_size_{suffix}"))
        if book is None:
            return None
        if symbol not in self._contracts:
            self._contracts.register(Contract(
                symbol=symbol, underlier=underlier, multiplier=self.multiplier,
                right=right, strike=strike, expiry=expiry,
            ))
        return book

    @staticmethod
    def _book(at: datetime, symbol: str, bid, ask, bid_size, ask_size) -> OrderBook | None:
        """A quote only counts when both sides are there and the right way round."""
        try:
            bid, ask = float(bid or 0.0), float(ask or 0.0)
        except (TypeError, ValueError):
            return None
        if bid <= 0.0 or ask <= bid:
            return None
        return OrderBook(
            time_stamp=at, symbol=symbol,
            bids=(BookLevel(bid, float(bid_size or 0.0)),),
            asks=(BookLevel(ask, float(ask_size or 0.0)),),
        )

    def _rows(self) -> Iterable[dict]:
        """Reads the download as it comes: a zip of CSVs, a gzip, or a plain file."""
        if self.path.suffix == ".zip":
            with zipfile.ZipFile(self.path) as archive:
                wanted = [n for n in archive.namelist() if n.lower().endswith(".csv")]
                # The archive ships both licence variants; the historical one has real index values.
                wanted.sort(key=lambda n: "no_cgi" in n)
                with archive.open(wanted[0]) as handle:
                    text = handle.read().decode("utf-8", errors="replace")
            yield from csv.DictReader(text.splitlines())
            return

        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt", newline="", encoding="utf-8", errors="replace") as handle:
            yield from csv.DictReader(handle)
