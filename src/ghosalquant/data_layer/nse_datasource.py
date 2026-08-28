import csv
import gzip
from datetime import datetime
from pathlib import Path
from typing import AsyncIterable, Iterable

from ..core.contracts import Contract, ContractRegistry, Right
from ..core.types import Bar, MarketEvent
from .datasource_base import DataSource

# NSE lot sizes are per contract and change over time; these are the 2019 index sizes.
DEFAULT_LOT_SIZES = {"NIFTY": 75.0, "BANKNIFTY": 20.0}
NSE_DATE = "%d-%b-%Y"


class NSEDataSource(DataSource):
    """
    NSE end-of-day futures and options, from the exchange bhavcopy.

    One ``Bar`` per contract per session, plus the near future under the plain symbol, since the
    index does not trade. Settlement prices only: the file has no bid or ask and none are
    invented.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        symbol: str = "NIFTY",
        lot_size: float | None = None,
        min_open_interest: float = 0.0,
        max_tenor_days: int | None = None,
        strike_band: float | None = None,
        mark: str = "SETTLE_PR",
    ) -> None:
        self.path = Path(path)
        self.symbol = symbol.strip().upper()
        self.lot_size = lot_size or DEFAULT_LOT_SIZES.get(self.symbol, 1.0)
        self.min_open_interest = float(min_open_interest)
        self.max_tenor_days = max_tenor_days
        self.strike_band = strike_band
        self.mark = mark
        self._contracts = ContractRegistry()
        self._events: list[MarketEvent] | None = None

    @property
    def contracts(self) -> ContractRegistry:
        """What the options in the file are. Empty until the file has been read."""
        if self._events is None:
            self._events = self._load()
        return self._contracts

    async def __aiter__(self) -> AsyncIterable[MarketEvent]:
        if self._events is None:
            self._events = self._load()
        for event in self._events:
            yield event

    def option_symbol(self, expiry, right: Right, strike: float) -> str:
        """OCC-shaped where the root allows it, so a six-character root parses with ``from_occ``."""
        letter = "C" if right is Right.CALL else "P"
        return f"{self.symbol:<6}{expiry:%y%m%d}{letter}{round(strike * 1000):08d}"

    def _load(self) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        futures: dict[datetime, list[tuple]] = {}
        for row in self._rows():
            if row["SYMBOL"].strip().upper() != self.symbol:
                continue
            at = datetime.strptime(row["TIMESTAMP"].strip(), NSE_DATE)
            expiry = datetime.strptime(row["EXPIRY_DT"].strip(), NSE_DATE).date()
            instrument = row["INSTRUMENT"].strip().upper()

            if instrument.startswith("FUT"):
                futures.setdefault(at, []).append((expiry, self._prices(row)))
            elif instrument.startswith("OPT"):
                event = self._option(row, at, expiry)
                if event is not None:
                    events.append(event)

        # The index itself does not trade, so the nearest future stands in for it.
        for at, expiries in futures.items():
            expiry, prices = min(expiries, key=lambda item: item[0])
            events.append(self._bar(at, self.symbol, prices))

        events.sort(key=lambda event: (event.time_stamp, event.symbol != self.symbol, event.symbol))
        return events

    def _option(self, row: dict, at: datetime, expiry) -> Bar | None:
        if float(row.get("OPEN_INT") or 0.0) < self.min_open_interest:
            return None
        days = (expiry - at.date()).days
        if days <= 0 or (self.max_tenor_days is not None and days > self.max_tenor_days):
            return None
        prices = self._prices(row)
        if prices[-1] <= 0.0:
            return None

        strike = float(row["STRIKE_PR"])
        right = Right.CALL if row["OPTION_TYP"].strip().upper() == "CE" else Right.PUT
        symbol = self.option_symbol(expiry, right, strike)
        if symbol not in self._contracts:
            self._contracts.register(Contract(
                symbol=symbol, underlier=self.symbol, multiplier=self.lot_size,
                right=right, strike=strike, expiry=expiry,
            ))
        return self._bar(at, symbol, prices)

    def _prices(self, row: dict) -> tuple[float, float, float, float]:
        """Open, high, low and the mark. A file without intraday columns is flat at the mark."""
        close = float(row.get(self.mark) or row.get("CLOSE") or 0.0)
        try:
            return (float(row["OPEN"]), float(row["HIGH"]), float(row["LOW"]), close)
        except (KeyError, TypeError, ValueError):
            return (close, close, close, close)

    @staticmethod
    def _bar(at: datetime, symbol: str, prices) -> Bar:
        open_, high, low, close = prices
        return Bar(time_stamp=at, symbol=symbol, open=open_, high=high, low=low, close=close,
                   volume=0.0)

    def _rows(self) -> Iterable[dict]:
        opener = gzip.open if self.path.suffix == ".gz" else open
        with opener(self.path, "rt", newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)
