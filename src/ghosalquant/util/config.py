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

class DataSrcType(str, Enum):
    YFINANCE = 'yfinance'
    POLYGON = 'polygon'

@dataclass(frozen=True, slots=True)
class DataConfig:
    source: DataSrcType
    yfinance: YFinanceConfig | None = None
    polygon: PolygonConfig | None = None

@dataclass(frozen=True, slots=True)
class TimeConfig:
    start_date: date
    end_date: date
    session_start_time: time
    session_end_time: time

@dataclass(frozen=True, slots=True)
class Config:
    system: SystemConfig
    data: DataConfig
    time_range: TimeConfig
    risk: RiskConfig

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
        else:
            raise ConfigError(f"DATA.source must be one of {[str(e) for e in DataSrcType]}. Got: {data_source_str}")

        yfinance_cfg: YFinanceConfig | None = None
        polygon_cfg: PolygonConfig | None = None

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

        data_config = DataConfig(
            source=data_source,
            yfinance=yfinance_cfg,
            polygon=polygon_cfg
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

        return Config(system=system_config, data=data_config, time_range=time_config, risk=RiskConfig(rules=rules))

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
