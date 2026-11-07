import os
from dataclasses import dataclass, field
from datetime import date, time, datetime
from enum import Enum
from typing import Any
import yaml
from dotenv import find_dotenv, load_dotenv
from pathlib import Path

ALLOWED_TFS = {"1Min", "5Min", "15Min", "30Min", "1H", "2H", "4H", "1D", "1W", "1M"}

class ConfigError(Exception):
    ...


@dataclass(frozen=True, slots=True)
class RiskRuleConfig:
    name: str
    params: dict = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class RiskConfig:
    rules: list[RiskRuleConfig] = field(default_factory=list)
    # Share of equity a target-weight buy leaves in cash, for the move before it fills and costs.
    cash_buffer: float = 0.01

    def __post_init__(self) -> None:
        if not 0.0 <= self.cash_buffer < 1.0:
            raise ConfigError(f"RISK.cash_buffer must be in [0, 1). Got: {self.cash_buffer}")

@dataclass(frozen=True, slots=True)
class SystemConfig:
    symbols: list[str] = field(default_factory=lambda: ["ASSET"])
    main_timeframe: str = "1D"
    execution_timeframe: str = "1D"
    fresh_data: bool = False
    # False runs with no risk rules, ignoring RISK.
    use_risk_manager: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.symbols, str):
            object.__setattr__(self, "symbols", [self.symbols])
        if not self.symbols:
            raise ConfigError("SYSTEM.symbols must name at least one symbol.")
        _validate_tf(self.main_timeframe, "SYSTEM.main_timeframe")
        _validate_tf(self.execution_timeframe, "SYSTEM.execution_timeframe")

@dataclass(frozen=True, slots=True)
class YFinanceConfig:
    ...

@dataclass(frozen=True, slots=True)
class PolygonConfig:
    api_key: str
    parquet_path: str
    base_resolution: str = "1Min" # The resolution of the underlying data

# Beside the code that writes them, so a run from another directory still finds them.
_DATA_DIR = Path(__file__).resolve().parents[1] / "data_layer" / "data"


@dataclass(frozen=True, slots=True)
class StaticDataConfig:
    """
    Where the static parquet datasets live, and what the Polygon downloader asks for.

    One-minute bars are canonical and coarser ones derive from them, so the aggregation is a
    multiplier and a timespan rather than a timeframe string.
    """

    following_parquet: str = str(_DATA_DIR / "following.parquet")
    main_parquet: str = str(_DATA_DIR / "main.parquet")
    futures_parquet: str = str(_DATA_DIR / "futures.parquet")
    treasury_parquet: str = str(_DATA_DIR / "treasury.parquet")
    agg_multiplier: int = 1
    agg_timespan: str = "minute"
    start_lookback_days: int = 365 * 2 + 1

    def __post_init__(self) -> None:
        if self.agg_multiplier <= 0:
            raise ConfigError("DATA.static.agg_multiplier must be > 0")
        if self.start_lookback_days <= 0:
            raise ConfigError("DATA.static.start_lookback_days must be > 0")
        if not self.agg_timespan.strip():
            raise ConfigError("DATA.static.agg_timespan must not be empty")


# "iex" is what a free account gets; "sip" is the full tape. Option feeds carry no bars.
ALPACA_STOCK_FEEDS = ("iex", "sip", "delayed_sip")
ALPACA_OPTION_FEEDS = ("indicative", "opra")


@dataclass(frozen=True, slots=True)
class AlpacaConfig:
    """
    Credentials and endpoints for Alpaca.

    ``endpoint`` is the trading REST base, paper by default: the live one places real orders and
    the two differ only by a hostname. ``feed`` picks the data quality and the market.
    """

    api_key: str
    api_secret: str
    endpoint: str = "https://paper-api.alpaca.markets"
    feed: str = "iex"

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_secret:
            raise ConfigError("DATA.alpaca needs api_key and api_secret")
        if self.feed not in ALPACA_STOCK_FEEDS + ALPACA_OPTION_FEEDS:
            raise ConfigError(
                f"DATA.alpaca.feed must be one of "
                f"{ALPACA_STOCK_FEEDS + ALPACA_OPTION_FEEDS}. Got: {self.feed}"
            )

    @property
    def is_option_feed(self) -> bool:
        return self.feed in ALPACA_OPTION_FEEDS

    @property
    def is_paper(self) -> bool:
        return "paper" in self.endpoint


@dataclass(frozen=True, slots=True)
class LiveIBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 2
    bar_resolution_sec: int = 1
    depth: int | None = None
    book_resolution_sec: float = 0.0

@dataclass(frozen=True, slots=True)
class LivePolygonConfig:
    api_key: str
    base_resolution: str = "1Min" # 1Sec, 1Min, ...

class DataSrcType(str, Enum):
    YFINANCE = 'yfinance'
    LIVE_IBKR = 'live_ibkr'
    POLYGON = 'polygon'
    LIVE_ALPACA = 'live_alpaca'

MARKET_EVENT_KINDS = ("bars", "order_books", "trades")


@dataclass(frozen=True, slots=True)
class DataConfig:
    source: DataSrcType
    yfinance: YFinanceConfig | None = None
    polygon: PolygonConfig | None = None
    live_ibkr: LiveIBKRConfig | None = None
    alpaca: AlpacaConfig | None = None
    static: StaticDataConfig = field(default_factory=StaticDataConfig)
    events: tuple[str, ...] = ("bars",)

@dataclass(frozen=True, slots=True)
class TimeConfig:
    start_date: date
    end_date: date
    session_start_time: time
    session_end_time: time

class BrokerType(str, Enum):
    SIMULATED = 'simulated'
    IBKR = 'ibkr'

class BrokerMode(str, Enum):
    PAPER = 'paper'
    LIVE = 'live'

@dataclass(frozen=True, slots=True)
class SimBrokerConfig:
    spread_bps: float = 2.0
    slippage_mean_bps: float = 0.5
    slippage_std_bps: float = 1.0
    commission_per_share: float = 0.0
    partial_fill_probability: float = 0.0
    reject_probability: float = 0.0
    max_fill_pct_of_bar_volume: float = 0.25
    impact_bps_at_full_participation: float = 10.0
    min_latency_ms: int = 0
    max_latency_ms: int = 0
    seed: int | None = None

@dataclass(frozen=True, slots=True)
class IBKRBrokerConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1

@dataclass(frozen=True, slots=True)
class BrokerConfig:
    type: BrokerType = BrokerType.SIMULATED
    mode: BrokerMode = BrokerMode.PAPER
    sim: SimBrokerConfig | None = None
    ibkr: IBKRBrokerConfig | None = None

    def __post_init__(self) -> None:
        # The same simulation a YAML config with no sim block gets.
        if self.type == BrokerType.SIMULATED and self.sim is None:
            object.__setattr__(self, "sim", SimBrokerConfig())

@dataclass(frozen=True, slots=True)
class Config:
    """
    Everything a run is configured with. ``Config()`` is a working backtest setup, and
    ``Config.from_yaml`` reads one from a file. ``data`` and ``time_range`` are checked only
    when bars are loaded from a source.
    """
    system: SystemConfig = field(default_factory=SystemConfig)
    data: DataConfig | None = None
    time_range: TimeConfig | None = None
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig | None = field(default_factory=BrokerConfig)

    @classmethod
    def from_yaml(cls, file_path: str | Path) -> "Config":
        """Read a config file. See ``load_config``."""
        return load_config(file_path)

# Validation Helpers
def _assert_map(raw: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping.")
    return raw

def _assert_str(raw: Any, path: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{path} must be a string that is not empty.")
    return raw

def _validate_tf(timeframe: str, path: str) -> str:
    tf = str(timeframe)
    if tf not in ALLOWED_TFS:
        raise ConfigError(f"{path} must be one of {sorted(ALLOWED_TFS)}. Got: {tf}")
    return tf


def _parse_broker_type(raw: Any, path: str) -> BrokerType:
    broker_type = _assert_str(raw, path).strip().lower()
    if broker_type == BrokerType.SIMULATED.value:
        return BrokerType.SIMULATED
    if broker_type == BrokerType.IBKR.value:
        return BrokerType.IBKR
    raise ConfigError(
        f"{path} must be one of {[e.value for e in BrokerType]}. Got: {broker_type}"
    )


def _parse_broker_mode(raw: Any, path: str) -> BrokerMode:
    mode = _assert_str(raw, path).strip().lower()
    if mode == BrokerMode.PAPER.value:
        return BrokerMode.PAPER
    if mode == BrokerMode.LIVE.value:
        return BrokerMode.LIVE
    if mode == "simulated":
        return BrokerMode.PAPER
    raise ConfigError(
        f"{path} must be one of {[BrokerMode.PAPER.value, BrokerMode.LIVE.value]}. Got: {mode}"
    )


def _parse_sim_broker_config(raw: Any) -> SimBrokerConfig:
    sim_raw = _assert_map(raw, "BROKERAGE.sim")
    return SimBrokerConfig(
        spread_bps=float(sim_raw.get("spread_bps", 2.0)),
        slippage_mean_bps=float(sim_raw.get("slippage_mean_bps", 0.5)),
        slippage_std_bps=float(sim_raw.get("slippage_std_bps", 1.0)),
        commission_per_share=float(sim_raw.get("commission_per_share", 0.0)),
        partial_fill_probability=float(sim_raw.get("partial_fill_probability", 0.0)),
        reject_probability=float(sim_raw.get("reject_probability", 0.0)),
        max_fill_pct_of_bar_volume=float(sim_raw.get("max_fill_pct_of_bar_volume", 0.25)),
        impact_bps_at_full_participation=float(sim_raw.get("impact_bps_at_full_participation", 10.0)),
        min_latency_ms=int(sim_raw.get("min_latency_ms", 0)),
        max_latency_ms=int(sim_raw.get("max_latency_ms", 0)),
        seed=None if sim_raw.get("seed") is None else int(sim_raw.get("seed")),
    )


def _parse_ibkr_broker_config(raw: Any) -> IBKRBrokerConfig:
    ibkr_raw = _assert_map(raw, "BROKERAGE.ibkr")
    return IBKRBrokerConfig(
        host=str(ibkr_raw.get("host", "127.0.0.1")),
        port=int(ibkr_raw.get("port", 7497)),
        client_id=int(ibkr_raw.get("client_id", 1)),
    )


def _parse_live_ibkr_config(raw: Any) -> LiveIBKRConfig:
    live_ibkr_raw = _assert_map(raw, "DATA.live_ibkr")
    depth_raw = live_ibkr_raw.get("depth")
    depth = None if depth_raw is None else int(depth_raw)
    if depth is not None and depth <= 0:
        raise ConfigError("DATA.live_ibkr.depth must be a positive integer.")
    return LiveIBKRConfig(
        host=str(live_ibkr_raw.get("host", "127.0.0.1")),
        port=int(live_ibkr_raw.get("port", 7497)),
        client_id=int(live_ibkr_raw.get("client_id", 2)),
        bar_resolution_sec=int(live_ibkr_raw.get("bar_resolution_sec", 1)),
        depth=depth,
        book_resolution_sec=float(live_ibkr_raw.get("book_resolution_sec", 0.0)),
    )


def _secret(raw: dict, field: str, variable: str, where: str) -> str:
    """
    A credential, from the environment and nowhere else.

    One found in the config is refused rather than ignored, which would leave it in the file.
    """
    if raw.get(field) is not None:
        raise ConfigError(
            f"{where}.{field} must not be in the config file. Move it to {variable} in .env "
            "(see .env.example) and delete it from here."
        )
    value = os.environ.get(variable, "").strip()
    if not value:
        raise ConfigError(
            f"{variable} is required in the environment for {where}. See .env.example."
        )
    return value


def _parse_static_config(raw: Any) -> StaticDataConfig:
    """The dataset locations and downloader settings, all optional with working defaults."""
    static_raw = _assert_map(raw, "DATA.static")
    defaults = StaticDataConfig()
    return StaticDataConfig(
        following_parquet=str(static_raw.get("following_parquet", defaults.following_parquet)),
        main_parquet=str(static_raw.get("main_parquet", defaults.main_parquet)),
        futures_parquet=str(static_raw.get("futures_parquet", defaults.futures_parquet)),
        treasury_parquet=str(static_raw.get("treasury_parquet", defaults.treasury_parquet)),
        agg_multiplier=int(static_raw.get("agg_multiplier", defaults.agg_multiplier)),
        agg_timespan=str(static_raw.get("agg_timespan", defaults.agg_timespan)).strip(),
        start_lookback_days=int(
            static_raw.get("start_lookback_days", defaults.start_lookback_days)
        ),
    )


def _parse_alpaca_config(raw: Any) -> AlpacaConfig:
    """Read the Alpaca block, letting the environment supply the credentials."""
    alpaca_raw = _assert_map(raw, "DATA.alpaca")
    return AlpacaConfig(
        api_key=_secret(alpaca_raw, "api_key", "ALPACA_API_KEY", "DATA.alpaca"),
        api_secret=_secret(alpaca_raw, "api_secret", "ALPACA_API_SECRET", "DATA.alpaca"),
        endpoint=str(alpaca_raw.get("endpoint", "https://paper-api.alpaca.markets")).strip(),
        feed=str(alpaca_raw.get("feed", "iex")).strip().lower(),
    )


def _parse_event_kinds(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ("bars",)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        raise ConfigError("DATA.events must be a non-empty string or list of strings.")
    kinds: list[str] = []
    for item in raw:
        kind = _assert_str(item, "DATA.events").strip().lower()
        if kind not in MARKET_EVENT_KINDS:
            raise ConfigError(f"DATA.events entries must be one of {list(MARKET_EVENT_KINDS)}. Got: {kind}")
        if kind not in kinds:
            kinds.append(kind)
    return tuple(kinds)


def _parse_broker_config(raw: Any) -> BrokerConfig:
    if raw is None:
        return BrokerConfig()

    if isinstance(raw, list):
        if len(raw) == 0:
            return BrokerConfig()
        if len(raw) != 1:
            raise ConfigError("BROKERAGE currently supports exactly one broker entry.")
        raw = raw[0]

    broker_raw = _assert_map(raw, "BROKERAGE")
    broker_type = _parse_broker_type(broker_raw.get("type"), "BROKERAGE.type")
    broker_mode = _parse_broker_mode(broker_raw.get("mode", "paper"), "BROKERAGE.mode")

    if broker_type == BrokerType.SIMULATED:
        return BrokerConfig(
            type=broker_type,
            mode=broker_mode,
            sim=_parse_sim_broker_config(broker_raw.get("sim", {})),
        )

    return BrokerConfig(
        type=broker_type,
        mode=broker_mode,
        ibkr=_parse_ibkr_broker_config(broker_raw.get("ibkr")),
    )

def _parse_data_config(raw: Any) -> DataConfig:
    data_raw = _assert_map(raw, "DATA")

    data_source_str = _assert_str(data_raw.get("source"), "DATA.source").strip().lower()
    if data_source_str == "yfinance":
        data_source = DataSrcType.YFINANCE
    elif data_source_str == "polygon":
        data_source = DataSrcType.POLYGON
    elif data_source_str == "live_ibkr":
        data_source = DataSrcType.LIVE_IBKR
    elif data_source_str == "live_alpaca":
        data_source = DataSrcType.LIVE_ALPACA
    else:
        raise ConfigError(f"DATA.source must be one of {[str(e) for e in DataSrcType]}. Got: {data_source_str}")

    yfinance_cfg: YFinanceConfig | None = None
    polygon_cfg: PolygonConfig | None = None
    live_ibkr_cfg: LiveIBKRConfig | None = None
    alpaca_cfg: AlpacaConfig | None = None

    if data_source == DataSrcType.YFINANCE: # TODO: Add yfinance config options in the future if needed
        yf_raw = data_raw.get("yfinance", {}) or {}
        _assert_map(yf_raw, "DATA.yfinance")
        yfinance_cfg = YFinanceConfig()

    elif data_source == DataSrcType.POLYGON:
        polygon_raw = _assert_map(data_raw.get("polygon"), "DATA.polygon")

        api_key = _secret(polygon_raw, "api_key", "POLYGON_API_KEY", "DATA.polygon")
        parquet_path = _assert_str(polygon_raw.get("parquet_path"), "DATA.polygon.parquet_path")
        base_res = _validate_tf(str(polygon_raw.get("base_resolution", "1Min")), "DATA.polygon.base_resolution")

        polygon_cfg = PolygonConfig(
            api_key=api_key,
            parquet_path=parquet_path,
            base_resolution=base_res,
        )
    elif data_source == DataSrcType.LIVE_IBKR:
        live_ibkr_raw = data_raw.get("live_ibkr", {}) or {}
        live_ibkr_cfg = _parse_live_ibkr_config(live_ibkr_raw)
    elif data_source == DataSrcType.LIVE_ALPACA:
        alpaca_cfg = _parse_alpaca_config(data_raw.get("alpaca", {}) or {})

    return DataConfig(
        source=data_source,
        yfinance=yfinance_cfg,
        polygon=polygon_cfg,
        live_ibkr=live_ibkr_cfg,
        alpaca=alpaca_cfg,
        static=_parse_static_config(data_raw.get("static", {}) or {}),
        events=_parse_event_kinds(data_raw.get("events")),
    )


def _parse_time_config(raw: Any) -> TimeConfig:
    time_range = _assert_map(raw, "TIME_RANGE")
    return TimeConfig(
        start_date=datetime.strptime(time_range["start_date"], "%Y-%m-%d").date(),
        end_date=datetime.strptime(time_range["end_date"], "%Y-%m-%d").date(),
        session_start_time=datetime.strptime(time_range["session_start_time"], "%H:%M").time(),
        session_end_time=datetime.strptime(time_range["session_end_time"], "%H:%M").time()
    )


def _parse_risk_config(raw: Any) -> RiskConfig:
    risk_raw = _assert_map(raw or {}, "RISK")
    rules_raw = risk_raw.get("rules", []) or []
    if not isinstance(rules_raw, list):
        raise ConfigError("RISK.rules must be a list.")

    rules: list[RiskRuleConfig] = []
    for rule_cfg in rules_raw:
        if not isinstance(rule_cfg, dict):
            raise ConfigError("Each RISK rule must be a mapping.")

        if "name" not in rule_cfg:
            raise ConfigError("Each RISK rule must define a 'name' field.")

        rule_params = dict(rule_cfg)
        rule_name = rule_params.pop("name")
        rules.append(RiskRuleConfig(name=str(rule_name), params=rule_params))

    return RiskConfig(
        rules=rules,
        cash_buffer=float(risk_raw.get("cash_buffer", RiskConfig().cash_buffer)),
    )


# Config loader
def load_config(file_path: str | Path) -> Config:
    """
    Loads and parses user's config file.

    Reads a ``.env`` first — beside the config if there is one, else the nearest above the working
    directory — without overriding anything already set.
    """
    file_path: Path = Path(file_path).resolve()
    beside = file_path.parent / ".env"
    # find_dotenv searches from this module by default, which is inside the installed package.
    load_dotenv(beside if beside.is_file() else find_dotenv(usecwd=True))

    # Open the config file
    try:
        with open(file_path, 'r') as file:
            raw: dict[str, Any] = yaml.safe_load(file) or {}


        # ----- System ----- #
        system = _assert_map(raw.get("SYSTEM"), "SYSTEM")

        symbols_raw = system.get("symbols", None)
        if isinstance(symbols_raw, str):
            symbols = [symbols_raw]
        elif isinstance(symbols_raw, list):
            symbols = [str(s) for s in symbols_raw]
        else:
            raise ConfigError("SYSTEM.symbols must be str or list[str].")

        main_tf = _validate_tf(_assert_str(system.get("main_timeframe"), "SYSTEM.main_timeframe"), "SYSTEM.main_timeframe")
        exec_tf = _validate_tf(_assert_str(system.get("execution_timeframe"), "SYSTEM.execution_timeframe"), "SYSTEM.execution_timeframe")

        system_config = SystemConfig(
            symbols=symbols,
            main_timeframe=main_tf,
            execution_timeframe=exec_tf,
            fresh_data=bool(system.get("fresh_candles", False)),
            use_risk_manager=bool(system.get("use_risk_manager", True))
        )

        # DATA and TIME_RANGE are optional: a DataFrame backtest uses neither.
        return Config(
            system=system_config,
            data=None if raw.get("DATA") is None else _parse_data_config(raw["DATA"]),
            time_range=None if raw.get("TIME_RANGE") is None else _parse_time_config(raw["TIME_RANGE"]),
            risk=_parse_risk_config(raw.get("RISK")),
            broker=_parse_broker_config(raw.get("BROKERAGE")),
        )

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
