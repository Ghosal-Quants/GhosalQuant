from dataclasses import dataclass
from datetime import date, time, datetime
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
class ParquetConfig:
    path: str
    base_resolution: str = "1Min" # The resolution of the underlying data

@dataclass(frozen=True, slots=True)
class SystemConfig:
    symbols: list[str]
    main_timeframe: str
    execution_timeframe: str
    fresh_data: bool
    use_risk_manager: bool

@dataclass(frozen=True, slots=True)
class TimeConfig:
    start_date: date
    end_date: date
    session_start_time: time
    session_end_time: time

@dataclass(frozen=True, slots=True)
class Config:
    system: SystemConfig
    time_range: TimeConfig
    risk: RiskConfig
    parquet_store: ParquetConfig | None = None


def load_config(file_path: str | Path) -> Config:
    """Loads and parses user's config file"""
    file_path: Path = Path(file_path).resolve()

    # Open the config file
    try:
        with open(file_path, 'r') as file:
            raw: dict[str, Any] = yaml.safe_load(file) or {}


        # ----- System ----- #
        system = raw["SYSTEM"]

        symbols_raw = system.get("symbols", None)
        if isinstance(symbols_raw, str):
            symbols = [symbols_raw]
        elif isinstance(symbols_raw, list):
            symbols = [str(s) for s in symbols_raw]
        else:
            raise ConfigError("SYSTEM.symbols must be str or list[str].")

        system_config = SystemConfig(
            symbols=symbols,
            main_timeframe=system["main_timeframe"],
            execution_timeframe=system["execution_timeframe"],
            fresh_data=bool(system.get("fresh_candles", False)),
            use_risk_manager=bool(system.get("use_risk_manager", False))
        )

        # ----- Time ----- #
        time_range = raw["TIME_RANGE"]

        time_config = TimeConfig(
            start_date=datetime.strptime(time_range["start_date"], "%Y-%m-%d").date(),
            end_date=datetime.strptime(time_range["end_date"], "%Y-%m-%d").date(),
            session_start_time=datetime.strptime(time_range["session_start_time"], "%H:%M").time(),
            session_end_time=datetime.strptime(time_range["session_end_time"], "%H:%M").time()
        )

        # ----- Risk (optional) ----- #
        risk_raw = raw.get("RISK", {}) or {}
        rules_raw = risk_raw.get("rules", []) or []

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

        # ----- Parquet Store (optional) ----- #
        parquet_config: ParquetConfig | None = None
        parquet_raw = raw.get("PARQUET_STORE", None)
        if parquet_raw is not None:
            parquet_config = ParquetConfig(
                path=str(parquet_raw["path"]),
                base_resolution=str(parquet_raw.get("base_resolution", "1Min")),
            )

        return Config(system=system_config, time_range=time_config, risk=RiskConfig(rules=rules), parquet_store=parquet_config)

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
