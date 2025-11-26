import yaml
from pathlib import Path 
import sys
import pandas as pd
from src.compatibility.backtester import Backtester
from src.compatibility.strategies.test_strategy import example_strategy

from data.get_data_script import get_data_df

def load_config(file_path: str) -> dict[str, str]:
    """Loads and parses user's config file"""

    # filepath should be fixed relative to the main file 
    file_path: Path = Path(file_path).resolve()
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
    config_dict: dict[str, str] = load_config("config.yaml")
    print("User config loaded successfully")

    # if we could not load user settings - something is super wrong 
    if config_dict is None:
        sys.exit(1)


    # Now we have user settings we can download data 
    df: pd.DataFrame = get_data_df(config_dict)

    # Create an instance of the backtester
    # pass in strat to backtester
    bt = Backtester(df, example_strategy)

    # run the backtest
    bt.run_backtest()

if __name__ == "__main__":
    run()