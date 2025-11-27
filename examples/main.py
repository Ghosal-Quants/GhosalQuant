from datetime import datetime, timedelta
import sys
import pandas as pd
import yfinance as yf

from compatibility.legacybacktester import LegacyBacktester
from example_strategy import ExampleStrategy

from util.config import ConfigError, load_config

# TODO: Note that this is the LEGACY main. Should use DataFrameBacktester in future.

if __name__ == "__main__":
    # Load user's config file
    try:
        config_dict: dict[str, str] = load_config("config.yaml")
        print("User config loaded successfully")
    except ConfigError as e:
        print(f"[CONFIG ERR]: {e}")
        sys.exit(1)

    sym: str = config_dict["SYSTEM"]["target"]

    # Now we have user settings we can download data
    df_raw: pd.DataFrame = yf.download(sym, start=datetime.now() - timedelta(days=30), end=datetime.now(), progress=False, auto_adjust=True)


    if df_raw is None or df_raw.empty:
        print("[DATA ERR]: No data retrieved, exiting.")
        sys.exit(1)

    df: pd.DataFrame = df_raw

    if isinstance(df_raw.columns, pd.MultiIndex):
        if "Ticker" in df_raw.columns.names:
            df = df_raw.xs(sym, axis=1, level="Ticker").copy()

    # Create an instance of the backtester
    # pass in strat to backtester
    bt = LegacyBacktester(df=df, symbol=config_dict["SYSTEM"]["target"], strategy=ExampleStrategy())

    # run the backtest
    bt.run()

    print(df)
