from dataclasses import dataclass
from datetime import date, time, datetime
import yaml
from pathlib import Path


class ConfigError(Exception):
    ...


@dataclass(frozen=True, slots=True)
class SystemConfig:
    symbols: list[str]
    main_timeframe: str
    execution_timeframe: str
    fresh_data: bool


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


def load_config(file_path: str | Path) -> Config:
# def load_config(file_path: str | Path) -> dict[str, Any] | None:
    """Loads and parses user's config file"""

    # filepath should be fixed relative to the main file
    file_path: Path = Path(file_path).resolve()
    print(f"{file_path}")

    # Open the config file
    try:
        with open (file_path, 'r') as file:
            # config_data: dict['str', 'str'] = yaml.safe_load(file) #  dict
            raw: dict = yaml.safe_load(file) #  dict

        system = raw["SYSTEM"]
        time_range = raw["TIME_RANGE"]

        system_config = SystemConfig(
            symbols=[system["target"]],
            main_timeframe=system["main_timeframe"],
            execution_timeframe=system["execution_timeframe"],
            fresh_data=bool(system.get("fresh_candles", False))
        )

        time_config = TimeConfig(
            start_date=datetime.strptime(time_range["start_date"], "%Y-%m-%d").date(),
            end_date=datetime.strptime(time_range["end_date"], "%Y-%m-%d").date(),
            session_start_time=datetime.strptime(time_range["session_start_time"], "%H:%M").time(),
            session_end_time=datetime.strptime(time_range["session_end_time"], "%H:%M").time()
        )

        return Config(system=system_config, time_range=time_config)

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
