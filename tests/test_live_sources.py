from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from ib_insync import DOMLevel, TickByTickAllLast, TickAttribLast

from ghosalquant.core.types import Bar, OrderBook, PublicTrade
from ghosalquant.data_layer.datasource_factory import datasource_from_config
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
