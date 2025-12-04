import pandas as pd
import json
from typing import Dict, Any
import os
STOCK = "AAPL"

def get_data_df(config_dict: dict[str, str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """load some data and return a dataframe"""

    # If user defines fresh candles = download data 
    if config_dict["SYSTEM"]["fresh_candles"]:
        candle_data: Dict[str, Any] = download_candles(config_dict)
        # Wont inititally execute


    main_timeframe = config_dict["SYSTEM"]["main_timeframe"]
    execution_timeframe = config_dict["SYSTEM"]["execution_timeframe"]
    target = config_dict["SYSTEM"]["target"]

    # load the data from where it was downloaded - one for H1 , one for M5
    candle_data_big: pd.DataFrame = load_data(config_dict, target, main_timeframe)
    candle_data_small: pd.DataFrame = load_data(config_dict, target, execution_timeframe)

    # parse raw data into a dataframe - this should be for later 
    # df_big = parse_data(candle_data_big)
    # df_small = parse_data(candle_data_small)


    return candle_data_big, candle_data_small


def download_candles(config_dict: dict[str, str]) -> Dict[str, Any]:
    """ 
    download some candlestick data as json -> data/<some_file>.pkl??? 
    """

    # This is for the data team to handle.... 

    pass


def load_data(config_dict: Dict[str, str], stock: str, granularity: str) -> Dict[str, Any]:
    """
    Load data from where it is downloaded.
    
    Expected structure: data/{stock}/{stock}_{granularity}.pkl
    Example: data/AAPL/AAPL_H1.pkl

    should give back some json 
    """
    
    # 1. Define the base directory
    base_dir = "data"
    
    # 2. Construct the file path dynamically
    # This creates: data/AAPL/AAPL_H1.pkl
    file_name = f"{stock}_{granularity}.pkl"
    file_path = os.path.join(base_dir, stock, file_name)

    # 3. Check if file exists to prevent crashing later
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Data file not found at: {file_path}")

    # 4. Load the data (Using pandas read_pickle based on your hint)
    try:
        df = pd.read_pickle(file_path)
    except Exception as e:
        raise IOError(f"Failed to load pickle file: {e}")

    # 5. Return a dictionary containing metadata and the actual dataframe
    return df


def parse_data(raw_data: Dict[str, Any]) -> pd.DataFrame:

    # This is for the data team to handle.... 

    pass