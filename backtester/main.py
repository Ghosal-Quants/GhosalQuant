import yaml
from pathlib import Path 
import sys

def load_config() -> dict[str, str]:
    """Loads and parses user's config file"""

    # filepath should be fixed relative to the main file 
    file_path: Path = Path("config/config.yaml").resolve()
    print(f"{file_path=}")

    # Open the config file
    try: 
        with open (file_path, 'r') as file:
            config_data: dict['str', 'str'] = yaml.safe_load(file) # dict 
        return config_data
    except FileNotFoundError:
        print(f"Error: The file {file_path} was not found.")
        return None
    except yaml.YAMLError as exc:
        print(f"Error parsing YAML file: {exc}")
        return None


if __name__ == "__main__":

    # Load user's config file 
    if load_config() is None:
        sys.exit()

    

    # Parse user config 


    pass