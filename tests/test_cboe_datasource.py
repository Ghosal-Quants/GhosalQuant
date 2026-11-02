import asyncio
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from ghosalquant.core.contracts import Contract, Right
from ghosalquant.core.types import OrderBook
from ghosalquant.data_layer.cboe_datasource import CboeEODDataSource

# The real UnderlyingOptionsEODCalcs schema, in order.
HEADER = (
    "underlying_symbol,quote_date,root,expiration,strike,option_type,open,high,low,close,"
    "trade_volume,bid_size_1545,bid_1545,ask_size_1545,ask_1545,underlying_bid_1545,"
    "underlying_ask_1545,implied_underlying_price_1545,active_underlying_price_1545,"
    "implied_volatility_1545,delta_1545,gamma_1545,theta_1545,vega_1545,rho_1545,"
    "bid_size_eod,bid_eod,ask_size_eod,ask_eod,underlying_bid_eod,underlying_ask_eod,"
    "vwap,open_interest,delivery_code"
)


def _row(strike, kind, bid, ask, oi, expiration="2023-09-15", root="SPY", underlying="SPY"):
    return (
        f"{underlying},2023-08-25,{root},{expiration},{strike:.3f},{kind},"
        f"1.0,1.0,1.0,1.0,10,"
        f"10,{bid:.4f},10,{ask:.4f},440.7800,440.8000,0.0000,440.7900,"
        f"0.1800,0.5000,0.0100,-0.0500,0.2000,0.1000,"
        f"1,{bid - 0.05:.4f},37,{ask + 0.05:.4f},439.9400,439.9500,"
        f"1.0,{oi},"
    )


ROWS = [
    _row(440.0, "C", 5.10, 5.20, 12_000),
    _row(440.0, "P", 4.60, 4.70, 9_000),
    _row(460.0, "C", 0.35, 0.45, 300),                       # thin
    _row(400.0, "P", 0.10, 0.12, 8_000, expiration="2023-08-25"),   # expires today
    _row(445.0, "C", 0.00, 0.00, 5_000),                     # no quote
    _row(300.0, "C", 30.0, 31.0, 7_000, root="TSLA", underlying="TSLA"),
]


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("cboe") / "UnderlyingOptionsEODCalcs_2023-08-25.csv"
    path.write_text("\n".join([HEADER, *ROWS]) + "\n")
    return path


def _drain(source: CboeEODDataSource) -> list:
    async def go():
        return [event async for event in source]
    return asyncio.run(go())


def test_options_arrive_as_real_two_sided_books(sample):
    """The point of this source: the spread is measured, not modelled."""
    events = _drain(CboeEODDataSource(sample, symbols=["SPY"]))
    books = [e for e in events if e.symbol != "SPY"]

    assert books and all(isinstance(b, OrderBook) for b in books)
    assert all(b.is_two_sided() and not b.is_crossed() for b in books)
    call = next(b for b in books if b.symbol.startswith("SPY") and "C00440000" in b.symbol)
    assert (call.best_bid.price, call.best_ask.price) == (5.10, 5.20)
    assert (call.best_bid.quantity, call.best_ask.quantity) == (10.0, 10.0)


def test_the_underlier_comes_from_the_same_file(sample):
    """Its own bid and ask are in every row, so spot needs no second source to go stale against."""
    events = _drain(CboeEODDataSource(sample, symbols=["SPY"]))

    spot = [e for e in events if e.symbol == "SPY"]
    assert len(spot) == 1, "one underlier book a session, not one per option row"
    assert (spot[0].best_bid.price, spot[0].best_ask.price) == (440.78, 440.80)
    assert events[0].symbol == "SPY", "the spot leads, so a strategy has one before any option"


def test_the_1545_snapshot_and_the_close_differ(sample):
    at_1545 = {b.symbol: b for b in _drain(CboeEODDataSource(sample, symbols=["SPY"]))}
    at_close = {b.symbol: b for b in
                _drain(CboeEODDataSource(sample, symbols=["SPY"], snapshot="eod"))}

    symbol = next(s for s in at_1545 if s != "SPY")
    assert at_1545[symbol].best_bid.price != at_close[symbol].best_bid.price
    assert at_1545[symbol].time_stamp.time() == datetime(2023, 8, 25, 15, 45).time()
    assert at_close[symbol].time_stamp.time() == datetime(2023, 8, 25, 16, 0).time()

    with pytest.raises(ValueError, match="snapshot"):
        CboeEODDataSource(sample, snapshot="lunchtime")


def test_rows_without_a_usable_quote_are_dropped(sample):
    """A zero bid is not a market, and half a book would price a trade that could not happen."""
    events = _drain(CboeEODDataSource(sample, symbols=["SPY"]))

    assert not any("C00445000" in e.symbol for e in events)


def test_open_interest_and_tenor_filter_the_chain(sample):
    everything = _drain(CboeEODDataSource(sample, symbols=["SPY"]))
    liquid = _drain(CboeEODDataSource(sample, symbols=["SPY"], min_open_interest=5_000))

    assert not any("C00460000" in e.symbol for e in liquid), "the 300-lot strike is gone"
    assert len(liquid) < len(everything)

    # The 2023-08-25 expiry is zero days out, so a zero-day cap keeps only that chain.
    near = _drain(CboeEODDataSource(sample, symbols=["SPY"], max_tenor_days=0))
    assert all("230825" in e.symbol or e.symbol == "SPY" for e in near)


def test_it_reads_only_the_underlyings_asked_for(sample):
    events = _drain(CboeEODDataSource(sample, symbols=["SPY"]))
    assert not any(e.symbol.startswith("TSLA") for e in events)

    both = _drain(CboeEODDataSource(sample))
    assert any(e.symbol.startswith("TSLA") for e in both)


def test_contracts_are_registered_in_the_occ_form(sample):
    source = CboeEODDataSource(sample, symbols=["SPY"])
    _drain(source)

    symbol = source.option_symbol("SPY", date(2023, 9, 15), Right.CALL, 440.0)
    contract = source.contracts.get(symbol)
    assert (contract.right, contract.strike, contract.underlier) == (Right.CALL, 440.0, "SPY")
    assert contract.multiplier == 100.0
    assert Contract.from_occ(symbol, multiplier=100.0).expiry == date(2023, 9, 15)


def test_a_zipped_download_is_read_without_unpacking_it(tmp_path, sample):
    """Cboe ships a zip holding both licence variants; the historical one is the real data."""
    archive = tmp_path / "UnderlyingOptionsEODCalcs_2023-08-25.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("UnderlyingOptionsEODCalcs_2023-08-25_no_cgi_subscription.csv",
                   "\n".join([HEADER, *[r.replace("440.7800,440.8000", "0.0000,0.0000")
                                        for r in ROWS]]) + "\n")
        z.writestr("UnderlyingOptionsEODCalcs_2023-08-25_cgi_or_historical.csv",
                   "\n".join([HEADER, *ROWS]) + "\n")

    events = _drain(CboeEODDataSource(archive, symbols=["SPY"]))
    spot = next(e for e in events if e.symbol == "SPY")
    assert spot.best_bid.price == 440.78, "the no-cgi file zeroes the index; take the other one"
