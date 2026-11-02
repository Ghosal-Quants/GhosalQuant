import asyncio
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from ib_insync import DOMLevel, Stock, TickByTickAllLast, TickAttribLast

from ghosalquant.core.contracts import Contract, ContractRegistry
from ghosalquant.core.types import Bar, OrderBook, PublicTrade
from ghosalquant.data_layer.datasource_factory import datasource_from_config
from ghosalquant.data_layer.live_alpaca_datasource import (
    LiveAlpacaDataSource,
    to_alpaca,
    to_occ,
)
from ghosalquant.data_layer.live_ibkr_datasource import LiveIBKRDataSource, book_from_ticker, trades_from_ticks
from ghosalquant.data_layer.live_polygon_datasource import LivePolygonDataSource
from ghosalquant.util.config import ConfigError, load_config


# ----- Polygon ----- #

def _polygon(**kwargs) -> LivePolygonDataSource:
    settings = dict(api_key="key", symbols=["aapl", "MSFT"])
    settings.update(kwargs)
    return LivePolygonDataSource(**settings)


def test_polygon_subscription_channels_follow_the_events_selector():
    assert _polygon().subscription_params() == "AM.AAPL,AM.MSFT"
    assert _polygon(base_resolution="1SEC", events=["bars", "quotes", "trades"]).subscription_params() == (
        "A.AAPL,A.MSFT,Q.AAPL,Q.MSFT,T.AAPL,T.MSFT"
    )
    assert _polygon(events=["trades"]).events == ("trades",)
    with pytest.raises(ValueError):
        _polygon(events=["candles"])
    with pytest.raises(ValueError):
        _polygon(events=[])


def test_polygon_parses_quotes_into_one_level_books():
    source = _polygon(events=["quotes"])
    book = source.parse_event({"ev": "Q", "sym": "AAPL", "bp": 170.0, "bs": 3, "ap": 170.05, "as": 5, "t": 1610000000000, "q": 77})

    assert isinstance(book, OrderBook)
    assert book.time_stamp == datetime(2021, 1, 7, 6, 13, 20, tzinfo=timezone.utc)
    assert book.best_bid.price == 170.0 and book.best_bid.quantity == 3.0
    assert book.best_ask.price == 170.05 and book.best_ask.quantity == 5.0
    assert book.sequence == 77

    one_sided = source.parse_event({"ev": "Q", "sym": "AAPL", "bp": 170.0, "bs": 3, "t": 1610000000000})
    assert one_sided is not None and one_sided.best_ask is None
    assert source.parse_event({"ev": "Q", "sym": "AAPL", "t": 1610000000000}) is None
    assert source.parse_event({"ev": "T", "sym": "AAPL", "p": 1.0, "s": 1, "t": 1610000000000}) is None


def test_polygon_parses_trades_and_bars():
    source = _polygon(events=["bars", "trades"])
    trade = source.parse_event({"ev": "T", "sym": "AAPL", "i": "529835", "p": 170.02, "s": 100, "t": 1610000000500})
    assert isinstance(trade, PublicTrade)
    assert trade.price == 170.02 and trade.quantity == 100.0 and trade.trade_id == "529835"
    assert trade.time_stamp == datetime(2021, 1, 7, 6, 13, 20, 500000, tzinfo=timezone.utc)
    assert source.parse_event({"ev": "T", "sym": "AAPL", "p": 170.02, "s": 0, "t": 1610000000500}) is None

    bar = source.parse_event({"ev": "AM", "sym": "AAPL", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 10, "e": 1610000060000})
    assert isinstance(bar, Bar) and bar.close == 1.5
    assert source.parse_event({"ev": "Q", "sym": "AAPL", "bp": 1.0, "bs": 1, "t": 1610000000000}) is None


# ----- IBKR ----- #

def test_ibkr_book_from_ticker_aggregates_sorts_and_cuts_depth():
    ticker = SimpleNamespace(
        domBids=[DOMLevel(100.0, 5.0, "A"), DOMLevel(99.9, 7.0, "B"), DOMLevel(100.0, 3.0, "C"), DOMLevel(99.8, 1.0, "D")],
        domAsks=[DOMLevel(100.2, 4.0, "A"), DOMLevel(100.1, 2.0, "B"), DOMLevel(0.0, 9.0, "X")],
    )
    now = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)
    book = book_from_ticker(ticker, symbol="AAPL", time_stamp=now, depth=2)

    assert book is not None
    assert [(level.price, level.quantity) for level in book.bids] == [(100.0, 8.0), (99.9, 7.0)]
    assert [(level.price, level.quantity) for level in book.asks] == [(100.1, 2.0), (100.2, 4.0)]
    assert book.symbol == "AAPL" and book.time_stamp == now

    assert book_from_ticker(SimpleNamespace(domBids=[], domAsks=[]), symbol="AAPL", time_stamp=now) is None


def test_ibkr_trades_from_ticks():
    when = datetime(2025, 1, 6, 14, 30, 1, tzinfo=timezone.utc)
    ticks = [
        TickByTickAllLast(1, when, 100.5, 200.0, TickAttribLast(), "NYSE", ""),
        TickByTickAllLast(1, when, 100.6, 0.0, TickAttribLast(), "NYSE", ""),
        TickByTickAllLast(1, None, 100.7, 50.0, TickAttribLast(), "NYSE", ""),
    ]
    fallback = datetime(2025, 1, 6, 14, 30, 2, tzinfo=timezone.utc)
    trades = trades_from_ticks("AAPL", ticks, fallback_time=fallback)

    assert [(t.price, t.quantity, t.time_stamp) for t in trades] == [(100.5, 200.0, when), (100.7, 50.0, fallback)]
    assert all(isinstance(t, PublicTrade) and t.symbol == "AAPL" for t in trades)


def test_ibkr_data_source_event_selection_and_depth_defaults():
    bars_only = LiveIBKRDataSource(symbols=["AAPL"])
    assert bars_only.events == ("bars",) and bars_only.depth is None

    with_books = LiveIBKRDataSource(symbols=["AAPL"], events=["order_books", "trades"])
    assert with_books.events == ("order_books", "trades") and with_books.depth == 5

    explicit = LiveIBKRDataSource(symbols=["AAPL"], events=["bars", "order_books"], depth=10, book_resolution_sec=0.5)
    assert explicit.depth == 10 and explicit.book_resolution_sec == 0.5

    with pytest.raises(ValueError):
        LiveIBKRDataSource(symbols=["AAPL"], events=["quotes"])
    with pytest.raises(ValueError):
        LiveIBKRDataSource(symbols=["AAPL"], depth=0)


def test_ibkr_subscribes_to_registered_options_and_keeps_them_apart_from_the_underlier():
    occ = "AAPL  260116C00150000"
    registry = ContractRegistry([Contract.from_occ(occ)])
    source = LiveIBKRDataSource(symbols=[occ, "AAPL"], contracts=registry)

    option, stock = source._contracts
    assert (option.secType, option.strike, option.right) == ("OPT", 150.0, "C")
    assert option.lastTradeDateOrContractMonth == "20260116"
    assert stock.secType == "STK"

    # Both carry ticker "AAPL", and an unqualified option has no local symbol to tell them apart.
    assert source.symbols == (occ, "AAPL")
    assert source._name_of(option) == occ
    assert source._name_of(stock) == "AAPL"


def test_ibkr_symbols_are_unchanged_without_a_registry():
    assert LiveIBKRDataSource(symbols=["aapl", Stock("MSFT", "SMART", "USD")]).symbols == (
        "AAPL", "MSFT"
    )


# ----- Config ----- #

_CONFIG = """
SYSTEM:
  symbols: ["AAPL"]
  main_timeframe: "1Min"
  execution_timeframe: "1Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "{source}"
  events: {events}
  yfinance: {{}}
  live_ibkr:
    host: "127.0.0.1"
    port: 4002
    client_id: 7
    bar_resolution_sec: 5
    depth: 10
    book_resolution_sec: 0.25
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
"""


def _load(source: str, events: str):
    with TemporaryDirectory() as tmp_dir:
        cfg_path = Path(tmp_dir) / "config.yaml"
        cfg_path.write_text(_CONFIG.format(source=source, events=events))
        return load_config(cfg_path)


def test_config_parses_events_and_depth():
    config = _load("live_ibkr", '["bars", "order_books", "trades", "bars"]')
    assert config.data.events == ("bars", "order_books", "trades")
    assert config.data.live_ibkr.depth == 10
    assert config.data.live_ibkr.book_resolution_sec == 0.25

    assert _load("yfinance", "null").data.events == ("bars",)
    assert _load("yfinance", '"bars"').data.events == ("bars",)

    with pytest.raises(ConfigError):
        _load("live_ibkr", '["quotes"]')
    with pytest.raises(ConfigError):
        _load("live_ibkr", "[]")


def test_factory_passes_events_and_depth_to_live_ibkr_and_rejects_them_for_bar_sources():
    source = datasource_from_config(_load("live_ibkr", '["order_books", "trades"]'))
    assert isinstance(source, LiveIBKRDataSource)
    assert source.events == ("order_books", "trades")
    assert source.depth == 10
    assert source.book_resolution_sec == 0.25

    with pytest.raises(ConfigError):
        datasource_from_config(_load("yfinance", '["bars", "trades"]'))


# ----- Alpaca ----- #

def _alpaca(**kwargs) -> LiveAlpacaDataSource:
    settings = dict(api_key="k", api_secret="s", symbols=["aapl", "MSFT"])
    settings.update(kwargs)
    return LiveAlpacaDataSource(**settings)


def test_alpaca_needs_no_sdk_until_it_connects(monkeypatch):
    """
    The SDK is an optional dependency, so importing it eagerly would break every run without it.

    Configuring a source has to work regardless; only connecting needs the package, and it should
    say so rather than failing on an import three frames down.
    """
    import builtins

    real_import = builtins.__import__

    def refuse_alpaca(name, *args, **kwargs):
        if name.split(".")[0] == "alpaca":
            raise ImportError("no module named alpaca")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_alpaca)

    source = _alpaca(events=["bars", "order_books", "trades"], feed="sip")
    assert source.feed == "sip"
    with pytest.raises(ImportError, match=r"ghosalquant\[alpaca\]"):
        source._build_stream()


def test_alpaca_event_selection_and_feeds():
    assert _alpaca().events == ("bars",)
    assert _alpaca(events=["order_books", "trades"], feed="opra").is_option_feed is True
    assert _alpaca(feed="delayed_sip").is_option_feed is False

    with pytest.raises(ValueError, match="feed"):
        _alpaca(feed="nonsense")
    with pytest.raises(ValueError, match="Unsupported event kinds"):
        _alpaca(events=["quotes"])
    with pytest.raises(ValueError, match="not bars"):
        _alpaca(events=["bars"], feed="opra")
    with pytest.raises(ValueError):
        _alpaca(symbols=[])


def test_an_alpaca_option_symbol_becomes_the_one_the_registry_knows():
    """
    Alpaca writes AAPL240315C00172500; the OCC pads the root to six.

    Left alone the same contract is two different symbols, and a fill would be credited to a
    position the portfolio does not think it has.
    """
    padded = to_occ("AAPL240315C00172500")

    assert padded == "AAPL  240315C00172500"
    assert len(padded) == 21
    assert to_alpaca(padded) == "AAPL240315C00172500"
    assert Contract.from_occ(padded).strike == 172.5
    assert to_occ("AAPL") == to_alpaca("AAPL") == "AAPL"
    assert _alpaca(symbols=["AAPL240315C00172500"], feed="opra",
                   events=["order_books"]).symbols == (padded,)


def test_alpaca_subscribes_to_the_channels_the_events_asked_for():
    source = _alpaca(events=["order_books", "trades"])
    channels = {}

    class _Stream:
        def subscribe_quotes(self, handler, *symbols): channels["quotes"] = symbols
        def subscribe_trades(self, handler, *symbols): channels["trades"] = symbols
        def subscribe_bars(self, handler, *symbols): channels["bars"] = symbols
        async def _run_forever(self): return None
        async def stop_ws(self): return None

    async def drain():
        source._build_stream = lambda: _Stream()
        async for _ in source:
            break

    asyncio.run(drain())

    assert set(channels) == {"quotes", "trades"}, "order_books is Alpaca's quote channel"
    assert channels["quotes"] == ("AAPL", "MSFT")


def test_alpaca_quotes_and_trades_become_books_and_prints():
    source = _alpaca(events=["order_books", "trades"])
    at = datetime(2026, 3, 4, 15, 30, tzinfo=timezone.utc)

    quote = SimpleNamespace(symbol="AAPL240315C00172500", timestamp=at, bid_price=1.20,
                            ask_price=1.24, bid_size=30, ask_size=12)
    book = source._parse_quote(quote)
    assert isinstance(book, OrderBook)
    assert book.symbol == "AAPL  240315C00172500"
    assert (book.best_bid.price, book.best_ask.price) == (1.20, 1.24)
    assert book.is_two_sided() and not book.is_crossed()

    trade = source._parse_trade(SimpleNamespace(symbol="AAPL", timestamp=at, price=190.5,
                                                size=100, id=42))
    assert (trade.symbol, trade.price, trade.quantity, trade.trade_id) == ("AAPL", 190.5, 100.0, "42")

    assert source._parse_quote(SimpleNamespace(symbol="AAPL", timestamp=at, bid_price=0.0,
                                               ask_price=1.0, bid_size=0, ask_size=1)) is None
    assert source._parse_trade(SimpleNamespace(symbol="AAPL", timestamp=None, price=1.0,
                                               size=1, id=None)) is None
