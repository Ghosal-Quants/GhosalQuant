from typing import Any
import yaml
from pathlib import Path

class ConfigError(Exception):
    ...

def load_config(file_path: str | Path) -> dict[str, Any] | None:
    """Loads and parses user's config file"""

    # filepath should be fixed relative to the main file
    file_path: Path = Path(file_path).resolve()
    print(f"{file_path=}")

    # Open the config file
    try:
        with open (file_path, 'r') as file:
            config_data: dict['str', 'str'] = yaml.safe_load(file) #  dict
        return config_data

    except FileNotFoundError:
        raise ConfigError(f"Error: The file {file_path} was not found.")

    except yaml.YAMLError as exc:
        raise ConfigError(f"Error parsing YAML file {file_path}: {exc}")
