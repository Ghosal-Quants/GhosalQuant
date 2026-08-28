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


def test_load_config_parses_the_alpaca_block(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "PKTEST")
    monkeypatch.setenv("ALPACA_API_SECRET", "shhh")
    config = _load(_alpaca_yaml("""  alpaca:
    endpoint: "https://api.alpaca.markets"
    feed: "sip\""""))

    assert config.data.source is DataSrcType.LIVE_ALPACA
    assert config.data.alpaca is not None
    assert (config.data.alpaca.api_key, config.data.alpaca.api_secret) == ("PKTEST", "shhh")
    assert config.data.alpaca.feed == "sip"
    assert config.data.alpaca.is_paper is False, "that endpoint places real orders"
    assert config.data.alpaca.is_option_feed is False


def test_a_credential_written_into_the_config_is_refused(monkeypatch):
    """
    Ignoring it would be worse than failing: the key would stay in the file regardless.

    Refusing says so while the author is still looking at it, before it is committed.
    """
    monkeypatch.setenv("ALPACA_API_KEY", "PKENV")
    monkeypatch.setenv("ALPACA_API_SECRET", "envsecret")

    for leak in ("api_key", "api_secret"):
        with pytest.raises(ConfigError, match="must not be in the config file"):
            _load(_alpaca_yaml(f"""  alpaca:
    {leak}: "leaked\""""))


def test_alpaca_credentials_can_come_from_the_environment(monkeypatch):
    """A key written into a config file gets committed sooner or later."""
    monkeypatch.setenv("ALPACA_API_KEY", "PKENV")
    monkeypatch.setenv("ALPACA_API_SECRET", "envsecret")

    config = _load(_alpaca_yaml("  alpaca: {}"))

    assert (config.data.alpaca.api_key, config.data.alpaca.api_secret) == ("PKENV", "envsecret")
    assert config.data.alpaca.is_paper is True
    assert config.data.alpaca.feed == "iex"


def test_alpaca_without_credentials_anywhere_is_refused(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)

    with pytest.raises(ConfigError, match="ALPACA_API_KEY is required"):
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


def test_a_dotenv_file_supplies_credentials_the_config_leaves_out(tmp_path, monkeypatch):
    """
    The point of .env.example: a config with no key in it still loads.

    load_config reads .env from the working directory, so a credential never has to be written
    into a file that gets committed.
    """
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_API_SECRET", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ALPACA_API_KEY=PKFROMFILE\nALPACA_API_SECRET=filesecret\n")

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_alpaca_yaml("  alpaca: {}"))
    config = load_config(cfg_path)

    assert config.data.alpaca.api_key == "PKFROMFILE"
    assert config.data.alpaca.api_secret == "filesecret"


def test_the_real_environment_beats_the_dotenv_file(tmp_path, monkeypatch):
    """Otherwise a stale .env would quietly override what a deployment actually set."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ALPACA_API_KEY=FROMFILE\nALPACA_API_SECRET=FROMFILE\n")
    monkeypatch.setenv("ALPACA_API_KEY", "FROMENV")
    monkeypatch.setenv("ALPACA_API_SECRET", "FROMENV")

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_alpaca_yaml("  alpaca: {}"))

    assert load_config(cfg_path).data.alpaca.api_key == "FROMENV"


def test_the_polygon_key_can_come_from_the_environment_too(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "POLYENV")
    yaml_text = """
SYSTEM:
  symbols: ["AAPL"]
  main_timeframe: "1Min"
  execution_timeframe: "1Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "polygon"
  polygon:
    parquet_path: "data/main.parquet"
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
"""
    assert _load(yaml_text).data.polygon.api_key == "POLYENV"

    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="POLYGON_API_KEY is required"):
        _load(yaml_text)

    monkeypatch.setenv("POLYGON_API_KEY", "POLYENV")
    with_key = yaml_text.replace("  polygon:", """  polygon:
    api_key: "inline\"""")
    with pytest.raises(ConfigError, match="must not be in the config file"):
        _load(with_key)


def test_the_template_lists_the_credentials_and_nothing_else():
    """
    .env.example is for secrets. Settings belong in config.yaml, which is meant to be committed.

    Derived from the loader rather than hardcoded, so a new credential that nobody documents, or
    a non-secret that creeps into the template, both fail here.
    """
    import re

    root = Path(__file__).resolve().parents[1]
    config_source = (root / "src" / "ghosalquant" / "util" / "config.py").read_text(encoding="utf-8")
    required = set(re.findall(r'_secret\([^,]+,\s*"[^"]+",\s*"([A-Z_]+)"', config_source))

    documented = {
        line.split("=", 1)[0].strip()
        for line in (root / ".env.example").read_text().splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    }

    assert required, "the scan found no credentials, so it is not testing anything"
    assert documented == required, (
        f"missing from .env.example: {sorted(required - documented)}; "
        f"not a credential: {sorted(documented - required)}"
    )


# ----- The static dataset settings ----- #

_STATIC_YAML = """
SYSTEM:
  symbols: ["AAPL"]
  main_timeframe: "1Min"
  execution_timeframe: "1Min"
  fresh_candles: false
  use_risk_manager: false
DATA:
  source: "yfinance"
  yfinance: {}
%s
TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2024-01-31"
  session_start_time: "09:00"
  session_end_time: "17:00"
RISK:
  rules: []
"""


def test_the_static_settings_default_without_a_block():
    """They are settings with working defaults, so a config that says nothing still runs."""
    static = _load(_STATIC_YAML % "").data.static

    assert static.agg_multiplier == 1
    assert static.agg_timespan == "minute"
    assert static.start_lookback_days == 731
    assert static.main_parquet.endswith("main.parquet")
    # Beside the code, not the working directory.
    assert Path(static.main_parquet).is_absolute()


def test_the_static_block_overrides_what_it_names():
    static = _load(_STATIC_YAML % """  static:
    main_parquet: "/data/mine.parquet"
    agg_multiplier: 5
    agg_timespan: "hour"
    start_lookback_days: 30""").data.static

    assert static.main_parquet == "/data/mine.parquet"
    assert (static.agg_multiplier, static.agg_timespan) == (5, "hour")
    assert static.start_lookback_days == 30
    assert static.futures_parquet.endswith("futures.parquet")


def test_nonsense_static_settings_are_refused():
    for block, message in (
        ('  static:\n    agg_multiplier: 0', "agg_multiplier"),
        ('  static:\n    start_lookback_days: -5', "start_lookback_days"),
        ('  static:\n    agg_timespan: "  "', "agg_timespan"),
    ):
        with pytest.raises(ConfigError, match=message):
            _load(_STATIC_YAML % block)


def test_the_scripts_read_the_config_rather_than_the_environment(monkeypatch):
    """
    These used to be read straight from os.environ, which put settings in .env alongside secrets.

    They come from DATA.static now, and the two scripts agree on where the data lives — before,
    one defaulted beside the code and the other relative to the working directory.
    """
    from ghosalquant.data_layer import listEditor, staticUpdater
    from ghosalquant.util.config import StaticDataConfig

    assert listEditor.MAIN_PARQUET == staticUpdater.MAIN_PARQUET

    monkeypatch.setenv("MAIN_PARQUET", "/from/the/environment.parquet")
    assert listEditor.MAIN_PARQUET != "/from/the/environment.parquet"

    settings = StaticDataConfig(main_parquet="/configured/main.parquet", agg_multiplier=15)
    try:
        listEditor.configure(settings)
        staticUpdater.configure(settings)
        assert listEditor.MAIN_PARQUET == "/configured/main.parquet"
        assert staticUpdater.MAIN_PARQUET == "/configured/main.parquet"
        assert staticUpdater.AGG_MULTIPLIER == 15
    finally:
        listEditor.configure(StaticDataConfig())
        staticUpdater.configure(StaticDataConfig())
