import asyncio
import gzip
from datetime import date
from pathlib import Path

import pytest

from ghosalquant.core.contracts import Contract, Right
from ghosalquant.core.types import Bar
from ghosalquant.data_layer.nse_datasource import NSEDataSource

DATASET = Path(__file__).resolve().parents[1] / "datasets" / "nse_nifty_fo_2019.csv.gz"

HEADER = "INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,CLOSE,SETTLE_PR,CONTRACTS,OPEN_INT,TIMESTAMP"
ROWS = [
    "FUTIDX,NIFTY,29-Aug-2019,0,XX,11015.35,11015.35,199881,19001400,01-AUG-2019",
    "FUTIDX,NIFTY,26-Sep-2019,0,XX,11066.60,11066.60,5283,893625,01-AUG-2019",
    "OPTIDX,NIFTY,08-Aug-2019,10800,CE,222.55,222.55,1000,90000,01-AUG-2019",
    "OPTIDX,NIFTY,08-Aug-2019,10800,PE,25.30,25.30,900,80000,01-AUG-2019",
    "OPTIDX,NIFTY,08-Aug-2019,11500,CE,1.05,1.05,10,100,01-AUG-2019",       # illiquid
    "OPTIDX,BANKNIFTY,08-Aug-2019,28000,CE,300.00,300.00,50,60000,01-AUG-2019",
]


@pytest.fixture(scope="module")
def sample(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("nse") / "nse.csv"
    path.write_text("\n".join([HEADER, *ROWS]) + "\n")
    return path


def _drain(source: NSEDataSource) -> list:
    async def go():
        return [event async for event in source]
    return asyncio.run(go())


def test_the_near_future_stands_in_for_the_index(sample):
    """The index itself does not trade, so a strategy needs something that does."""
    source = NSEDataSource(sample, symbol="NIFTY")
    events = _drain(source)

    underlier = [e for e in events if e.symbol == "NIFTY"]
    assert len(underlier) == 1, "one underlier bar a session, not one per future expiry"
    assert underlier[0].close == pytest.approx(11015.35), "the nearest expiry, not the next"


def test_options_become_contracts_the_registry_can_price(sample):
    source = NSEDataSource(sample, symbol="NIFTY")
    _drain(source)

    call = source.contracts.get(source.option_symbol(date(2019, 8, 8), Right.CALL, 10800.0))
    assert (call.right, call.strike, call.expiry) == (Right.CALL, 10800.0, date(2019, 8, 8))
    assert call.multiplier == 75.0, "the 2019 NIFTY lot"
    # OCC-shaped, so the rest of the system reads it without a special case.
    assert Contract.from_occ(call.symbol, multiplier=75.0).strike == 10800.0


def test_it_reads_only_the_symbol_asked_for(sample):
    source = NSEDataSource(sample, symbol="NIFTY")
    events = _drain(source)

    assert all("BANKNIFTY" not in e.symbol for e in events)
    assert any(c.underlier == "NIFTY" for c in source.contracts.contracts)


def test_illiquid_and_expired_contracts_are_left_out(sample):
    liquid = NSEDataSource(sample, symbol="NIFTY", min_open_interest=50_000)
    assert len(_drain(liquid)) < len(_drain(NSEDataSource(sample, symbol="NIFTY")))

    # Everything in the sample expires more than three days out.
    assert _drain(NSEDataSource(sample, symbol="NIFTY", max_tenor_days=3)) == [
        e for e in _drain(NSEDataSource(sample, symbol="NIFTY")) if e.symbol == "NIFTY"
    ]


def test_a_file_without_intraday_columns_is_flat_at_the_mark(sample):
    """The bhavcopy this was cut from kept only the settle, and none is invented."""
    source = NSEDataSource(sample, symbol="NIFTY")
    option = next(e for e in _drain(source) if e.symbol != "NIFTY")

    assert option.open == option.high == option.low == option.close


def test_gzip_is_read_the_same_as_plain_text(tmp_path, sample):
    plain = sample
    zipped = tmp_path / "nse.csv.gz"
    zipped.write_bytes(gzip.compress(plain.read_bytes()))

    assert [e.symbol for e in _drain(NSEDataSource(zipped, symbol="NIFTY"))] == [
        e.symbol for e in _drain(NSEDataSource(plain, symbol="NIFTY"))
    ]


# ----- The dataset that ships with the repo ----- #

@pytest.mark.skipif(not DATASET.exists(), reason="the NSE dataset is not present")
def test_the_shipped_dataset_loads_and_is_ordered():
    source = NSEDataSource(DATASET, min_open_interest=5_000, max_tenor_days=60)
    events = _drain(source)

    assert len(events) > 10_000
    assert all(isinstance(e, Bar) for e in events)
    assert all(a.time_stamp <= b.time_stamp for a, b in zip(events, events[1:]))

    sessions = {e.time_stamp.date() for e in events}
    assert len(sessions) == 69
    assert min(sessions) == date(2019, 8, 1) and max(sessions) == date(2019, 11, 15)
    # Every session leads with the underlier, so a strategy has a spot before any option quote.
    assert events[0].symbol == "NIFTY"
