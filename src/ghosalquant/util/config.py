from dataclasses import dataclass
from datetime import date, time, datetime
import yaml
from pathlib import Path


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


def load_config(file_path: str | Path) -> Config:
    """Loads and parses user's config file"""
    file_path: Path = Path(file_path).resolve()

    # Open the config file
    try:
        with open (file_path, 'r') as file:
            raw: dict = yaml.safe_load(file) #  dict

        system = raw["SYSTEM"]
        time_range = raw["TIME_RANGE"]

        system_config = SystemConfig(
            symbols=[system["target"]],
            main_timeframe=system["main_timeframe"],
            execution_timeframe=system["execution_timeframe"],
            fresh_data=bool(system.get("fresh_candles", False)),
            use_risk_manager=bool(system.get("use_risk_manager", False))
        )

        time_config = TimeConfig(
            start_date=datetime.strptime(time_range["start_date"], "%Y-%m-%d").date(),
            end_date=datetime.strptime(time_range["end_date"], "%Y-%m-%d").date(),
            session_start_time=datetime.strptime(time_range["session_start_time"], "%H:%M").time(),
            session_end_time=datetime.strptime(time_range["session_end_time"], "%H:%M").time()
        )

        risk_raw = raw.get("RISK", {}) or {}
        rules_raw = risk_raw.get("rules", []) or []

        rules: list[RiskRuleConfig] = []
        for item in rules_raw:
            if not isinstance(item, dict) or "name" not in item:
                raise ConfigError("Each item in RISK.rules must be a mapping with a 'name'.")
            name = str(item["name"])
            params = {k: v for k, v in item.items() if k != "name"}
            rules.append(RiskRuleConfig(name=name, params=params))

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

        return Config(system=system_config, time_range=time_config, risk=RiskConfig(rules=rules))

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
