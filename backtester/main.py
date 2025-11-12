import yaml
from pathlib import Path 
import sys
import pandas as pd

from data.get_data_script import get_data_df

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
    
def run():
    # Load user's config file 
    config_dict: dict[str, str] = load_config()

    # if we could not load user settings - something is super wrong 
    if config_dict is None:
        sys.exit(1)


    # Now we have user settings we can download data 
    df: pd.DataFrame = get_data_df(config_dict)


    print("user config loaded successfully")

    pass 

if __name__ == "__main__":
    run()