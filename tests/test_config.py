from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from ghosalquant.data_layer.datasource_factory import datasource_from_config
from ghosalquant.data_layer.live_alpaca_datasource import LiveAlpacaDataSource
from ghosalquant.data_layer.live_ibkr_datasource import LiveIBKRDataSource
from ghosalquant.util.config import (
    load_config,
    Config,
    ConfigError,
    DataSrcType,
    SystemConfig,
    TimeConfig,
    BrokerType,
    BrokerMode,
)

def test_load_config():
    config: Config = load_config(Path(__file__).with_name("test_config.yaml"))

    assert isinstance(config, Config)
    assert isinstance(config.system, SystemConfig)
    assert isinstance(config.time_range, TimeConfig)

    assert config.system.symbols == ["AAPL", "MSFT"]
    assert config.system.main_timeframe == "1H"
    assert config.system.execution_timeframe == "5Min"
    assert config.system.fresh_data is True

    assert config.time_range.start_date.isoformat() == "2023-01-01"
    assert config.time_range.end_date.isoformat() == "2024-06-30"
    assert config.time_range.session_start_time.isoformat() == "09:00:00"
    assert config.time_range.session_end_time.isoformat() == "17:00:00"

    assert config.broker is not None
    assert config.broker.type == BrokerType.SIMULATED
    assert config.broker.mode == BrokerMode.PAPER
    assert config.broker.sim is not None
    assert config.broker.sim.spread_bps == 3.0
    assert config.broker.sim.slippage_mean_bps == 1.5
    assert config.broker.sim.commission_per_share == 0.01


def test_load_config_ignores_inactive_broker_block():
    yaml_text = """
SYSTEM:
  symbols: ["AAPL"]
  main_timeframe: "1D"
  execution_timeframe: "5Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "yfinance"
  yfinance: {}
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
BROKERAGE:
  type: "simulated"
  mode: "paper"
  sim:
    spread_bps: 4.0
  ibkr:
    host: "127.0.0.1"
    port: 4002
    client_id: 99
"""

    with TemporaryDirectory() as tmp_dir:
        cfg_path = Path(tmp_dir) / "config.yaml"
        cfg_path.write_text(yaml_text)
        config = load_config(cfg_path)

    assert config.broker is not None
    assert config.broker.type == BrokerType.SIMULATED
    assert config.broker.mode == BrokerMode.PAPER
    assert config.broker.sim is not None
    assert config.broker.sim.spread_bps == 4.0
    assert config.broker.ibkr is None


def test_load_config_parses_live_ibkr_data_config():
    yaml_text = """
SYSTEM:
  symbols: ["AAPL", "MSFT"]
  main_timeframe: "1Min"
  execution_timeframe: "1Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "live_ibkr"
  live_ibkr:
    host: "192.168.1.10"
    port: 4002
    client_id: 7
    bar_resolution_sec: 5
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
"""

    with TemporaryDirectory() as tmp_dir:
        cfg_path = Path(tmp_dir) / "config.yaml"
        cfg_path.write_text(yaml_text)
        config = load_config(cfg_path)

    assert config.data.live_ibkr is not None
    assert config.data.live_ibkr.host == "192.168.1.10"
    assert config.data.live_ibkr.port == 4002
    assert config.data.live_ibkr.client_id == 7
    assert config.data.live_ibkr.bar_resolution_sec == 5


def test_datasource_from_config_builds_live_ibkr_data_source():
    yaml_text = """
SYSTEM:
  symbols: ["AAPL", "MSFT"]
  main_timeframe: "1Min"
  execution_timeframe: "1Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "live_ibkr"
  live_ibkr:
    host: "192.168.1.10"
    port: 4002
    client_id: 7
    bar_resolution_sec: 5
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
"""

    with TemporaryDirectory() as tmp_dir:
        cfg_path = Path(tmp_dir) / "config.yaml"
        cfg_path.write_text(yaml_text)
        config = load_config(cfg_path)

    data_src = datasource_from_config(config)

    assert isinstance(data_src, LiveIBKRDataSource)
    assert data_src.symbols == ("AAPL", "MSFT")
    assert data_src.host == "192.168.1.10"
    assert data_src.port == 4002
    assert data_src.client_id == 7
    assert data_src.bar_resolution_sec == 5


# ----- Alpaca ----- #

def _alpaca_yaml(block: str) -> str:
    return f"""
SYSTEM:
  symbols: ["AAPL", "MSFT"]
  main_timeframe: "1Min"
  execution_timeframe: "1Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "live_alpaca"
  events: ["order_books", "trades"]
{block}
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
"""


def _load(yaml_text: str):
    with TemporaryDirectory() as tmp_dir:
        cfg_path = Path(tmp_dir) / "config.yaml"
        cfg_path.write_text(yaml_text)
        return load_config(cfg_path)


def test_load_config_parses_the_alpaca_block():
    config = _load(_alpaca_yaml("""  alpaca:
    api_key: "PKTEST"
    api_secret: "shhh"
    endpoint: "https://api.alpaca.markets"
    feed: "sip\""""))

    assert config.data.source is DataSrcType.LIVE_ALPACA
    assert config.data.alpaca is not None
    assert (config.data.alpaca.api_key, config.data.alpaca.api_secret) == ("PKTEST", "shhh")
    assert config.data.alpaca.feed == "sip"
    assert config.data.alpaca.is_paper is False, "that endpoint places real orders"
    assert config.data.alpaca.is_option_feed is False


def test_alpaca_credentials_can_come_from_the_environment(monkeypatch):
    """A key written into a config file gets committed sooner or later."""
    monkeypatch.setenv("ALPACA_API_KEY", "PKENV")
    monkeypatch.setenv("ALPACA_API_SECRET", "envsecret")

    config = _load(_alpaca_yaml("  alpaca: {}"))

    assert (config.data.alpaca.api_key, config.data.alpaca.api_secret) == ("PKENV", "envsecret")
    # Nothing said otherwise, so it points at paper rather than at real money.
    assert config.data.alpaca.is_paper is True
    assert config.data.alpaca.feed == "iex"


def test_alpaca_without_credentials_anywhere_is_refused(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    with pytest.raises(ConfigError, match="ALPACA_API_KEY"):
        _load(_alpaca_yaml("  alpaca: {}"))


def test_an_option_feed_is_recognised_and_a_made_up_one_is_not(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")

    assert _load(_alpaca_yaml('  alpaca:\n    feed: "opra"')).data.alpaca.is_option_feed is True
    with pytest.raises(ConfigError, match="feed must be one of"):
        _load(_alpaca_yaml('  alpaca:\n    feed: "nonsense"'))


def test_datasource_from_config_builds_an_alpaca_source(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_API_SECRET", "s")
    source = datasource_from_config(_load(_alpaca_yaml("  alpaca: {}")))

    assert isinstance(source, LiveAlpacaDataSource)
    assert source.symbols == ("AAPL", "MSFT")
    assert source.events == ("order_books", "trades")
    assert source.feed == "iex"
