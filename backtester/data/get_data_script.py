import pandas as pd
import json
from typing import Dict, Any

def get_data_df(config_dict: dict[str, str]) -> pd.DataFrame:
    """load some data and return a dataframe"""

    # Download raw data based on user config 
    candle_data: Dict[str, Any] = download_candles()

    # parse raw data into a dataframe
    df = parse_data(candle_data)


    return df 


def download_candles(config_dict: dict[str, str]) -> Dict[str, Any]:
    """download some candlestick data as json"""

    # This is for the data team to handle.... 

    pass


def parse_data(raw_data: Dict[str, Any]) -> pd.DataFrame:

    # This is for the data team to handle.... 

    pass