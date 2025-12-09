import pandas as pd
import json
from typing import Dict, Any

def get_data_df(config_dict: dict[str, str]) -> pd.DataFrame:
    """load some data and return a dataframe"""

    # If user defines fresh candles = download data 
    if config_dict["SYSTEM"]["fresh_candles"]:
        candle_data: Dict[str, Any] = download_candles(config_dict)

    # oad the data from where it was downloaded
    candle_data: Dict[str, Any] = load_data(config_dict)

    # parse raw data into a dataframe
    df = parse_data(candle_data)

    return df 


def download_candles(config_dict: dict[str, str]) -> Dict[str, Any]:
    """ 
    download some candlestick data as json -> data/<some_file>.pkl??? 
    """

    # This is for the data team to handle.... 

    pass

def load_data(config_dict: dict[str, str]):
    """
    Load data from where it is downloaded - should be data/<some_file>.pkl???
    """

    pass


def parse_data(raw_data: Dict[str, Any]) -> pd.DataFrame:

    # This is for the data team to handle.... 

    pass