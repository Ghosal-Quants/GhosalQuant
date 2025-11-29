from datetime import datetime, timedelta
import sys
import pandas as pd
import yfinance as yf

from compatibility.legacybacktester import LegacyBacktester
from compatibility.test_strategy import example_strategy

from util.config import load_config, Config

# TODO: Note that this is the LEGACY main. Should use DataFrameBacktester in future.

if __name__ == "__main__":
    # Load user's config file
    config: Config = load_config("../config.yaml")

    sym: str = config.system.symbols[0]

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
    bt = LegacyBacktester(df=df, strategy=example_strategy)

    # run the backtest
    bt.run_backtest()

    print(df)
