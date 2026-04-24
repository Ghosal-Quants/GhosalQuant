from dataclasses import dataclass
from datetime import date, time, datetime
from enum import Enum
from typing import Any
import yaml
from pathlib import Path

ALLOWED_TFS = {"1Min", "5Min", "15Min", "30Min", "1H", "2H", "4H", "1D", "1W", "1M"}

class ConfigError(Exception):
    ...


@dataclass(frozen=True, slots=True)
class RiskRuleConfig:
    name: str
    params: dict

@dataclass(frozen=True, slots=True)
class RiskConfig:
    rules: list[RiskRuleConfig]

@dataclass(frozen=True, slots=True)
class SystemConfig:
    symbols: list[str]
    main_timeframe: str
    execution_timeframe: str
    fresh_data: bool
    use_risk_manager: bool

@dataclass(frozen=True, slots=True)
class YFinanceConfig:
    ...

@dataclass(frozen=True, slots=True)
class PolygonConfig:
    api_key: str
    parquet_path: str
    base_resolution: str = "1Min" # The resolution of the underlying data

@dataclass(frozen=True, slots=True)
class LiveIBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 2
    bar_resolution_sec: int = 1

@dataclass(frozen=True, slots=True)
class LivePolygonConfig:
    api_key: str
    base_resolution: str = "1Min" # 1Sec, 1Min, ...

class DataSrcType(str, Enum):
    YFINANCE = 'yfinance'
    LIVE_IBKR = 'live_ibkr'
    POLYGON = 'polygon'

@dataclass(frozen=True, slots=True)
class DataConfig:
    source: DataSrcType
    yfinance: YFinanceConfig | None = None
    polygon: PolygonConfig | None = None
    live_ibkr: LiveIBKRConfig | None = None

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
    type: BrokerType
    mode: BrokerMode
    sim: SimBrokerConfig | None = None
    ibkr: IBKRBrokerConfig | None = None

@dataclass(frozen=True, slots=True)
class Config:
    system: SystemConfig
    data: DataConfig
    time_range: TimeConfig
    risk: RiskConfig
    broker: BrokerConfig | None = None

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
    return LiveIBKRConfig(
        host=str(live_ibkr_raw.get("host", "127.0.0.1")),
        port=int(live_ibkr_raw.get("port", 7497)),
        client_id=int(live_ibkr_raw.get("client_id", 2)),
        bar_resolution_sec=int(live_ibkr_raw.get("bar_resolution_sec", 1)),
    )


def _default_broker_config() -> BrokerConfig:
    return BrokerConfig(
        type=BrokerType.SIMULATED,
        mode=BrokerMode.PAPER,
        sim=SimBrokerConfig(),
    )


def _parse_broker_config(raw: Any) -> BrokerConfig:
    if raw is None:
        return _default_broker_config()

    if isinstance(raw, list):
        if len(raw) == 0:
            return _default_broker_config()
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

# Config loader
def load_config(file_path: str | Path) -> Config:
    """Loads and parses user's config file"""
    file_path: Path = Path(file_path).resolve()

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
            use_risk_manager=bool(system.get("use_risk_manager", False))
        )

        # ----- Data ----- #
        data_raw = _assert_map(raw.get("DATA"), "DATA")

        data_source_str = _assert_str(data_raw.get("source"), "DATA.source").strip().lower()
        if data_source_str == "yfinance":
            data_source = DataSrcType.YFINANCE
        elif data_source_str == "polygon":
            data_source = DataSrcType.POLYGON
        elif data_source_str == "live_ibkr":
            data_source = DataSrcType.LIVE_IBKR
        else:
            raise ConfigError(f"DATA.source must be one of {[str(e) for e in DataSrcType]}. Got: {data_source_str}")

        yfinance_cfg: YFinanceConfig | None = None
        polygon_cfg: PolygonConfig | None = None
        live_ibkr_cfg: LiveIBKRConfig | None = None

        if data_source == DataSrcType.YFINANCE: # TODO: Add yfinance config options in the future if needed
            yf_raw = data_raw.get("yfinance", {}) or {}
            _assert_map(yf_raw, "DATA.yfinance")
            yfinance_cfg = YFinanceConfig()

        elif data_source == DataSrcType.POLYGON:
            polygon_raw = _assert_map(data_raw.get("polygon"), "DATA.polygon")

            api_key = _assert_str(polygon_raw.get("api_key"), "DATA.polygon.api_key")
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

        data_config = DataConfig(
            source=data_source,
            yfinance=yfinance_cfg,
            polygon=polygon_cfg,
            live_ibkr=live_ibkr_cfg,
        )

        # ----- Time ----- #
        time_range = _assert_map(raw.get("TIME_RANGE"), "TIME_RANGE")

        time_config = TimeConfig(
            start_date=datetime.strptime(time_range["start_date"], "%Y-%m-%d").date(),
            end_date=datetime.strptime(time_range["end_date"], "%Y-%m-%d").date(),
            session_start_time=datetime.strptime(time_range["session_start_time"], "%H:%M").time(),
            session_end_time=datetime.strptime(time_range["session_end_time"], "%H:%M").time()
        )

        # ----- Risk (optional) ----- #
        risk_raw = raw.get("RISK", {}) or {}
        risk_raw = _assert_map(risk_raw, "RISK")
        rules_raw = risk_raw.get("rules", []) or []
        if not isinstance(rules_raw, list):
            raise ConfigError("RISK.rules must be a list.")

        rules: list[RiskRuleConfig] = []
        for rule_cfg in rules_raw:
            if not isinstance(rule_cfg, dict):
                raise ConfigError("Each RISK rule must be a mapping.")

            if "name" not in rule_cfg:
                raise ConfigError("Each RISK rule must define a 'name' field.")

            rule_name = rule_cfg["name"]

            rule_params = dict(rule_cfg)
            rule_params.pop("name")

            rules.append(
                RiskRuleConfig(
                    name=str(rule_name),
                    params=rule_params,
                )
            )

        broker_config = _parse_broker_config(raw.get("BROKERAGE"))

        return Config(
            system=system_config,
            data=data_config,
            time_range=time_config,
            risk=RiskConfig(rules=rules),
            broker=broker_config,
        )

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
