import sys
import pandas as pd

from src.compatibility.backtester import Backtester
from src.compatibility.strategies.test_strategy import example_strategy
from src.compatibility.get_data_script import get_data_df

from src.util.config import ConfigError, load_config

def run() -> None:
    # Load user's config file
    try:
        config_dict: dict[str, str] = load_config("config.yaml")
        print("User config loaded successfully")
    except ConfigError as e:
        print(f"[CONFIG ERR]: {e}")
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