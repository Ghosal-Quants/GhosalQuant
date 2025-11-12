import pandas as pd
import json
from typing import Dict, Any
import sys

# sys.path.append('../')
# import data.get_data_df

def test_get_data_df():

    """

    This is a gift for the data team :)

    we need to agree on what our data will look like such that i can parse it correctly

    The most effeicient way to do this si to write a test for this function and then write the code 
    to pass the test.... 

    We will use pytest for the testing - it should be defined in my requirements.txt for use in a virtual environment

    This is what i kinda expect he raw data to look like 
    I might be wrong - discuss in the meeting

    mock_raw_data: Dict[str, Any] = {
        "timestamp": [1672531200000, 1672534800000, 1672538400000],
        "o": [1.0500, 1.0515, 1.0520],
        "h": [1.0520, 1.0530, 1.0535],
        "l": [1.0495, 1.0510, 1.0518],
        "c": [1.0515, 1.0520, 1.0530],
        "v": [100, 120, 90]
    }

    This is what the data SHOULD look like at function return - im happy to discuss in next meeting if anyone disagrees
    mock_output_df = pd.DataFrame({
        'time': pd.to_datetime([1672531200000, 1672534800000, 1672538400000], unit='ms'),
        'open': [1.0500, 1.0515, 1.0520],
        'high': [1.0520, 1.0530, 1.0535],
        'low': [1.0495, 1.0510, 1.0518],
        'close': [1.0515, 1.0520, 1.0530]
    }).set_index('time')


    We will discuss in the meeting what the df will look like and i will need someone on the data side to handle the API calls

    """

    # Example user settings 
    user_settings: Dict[str, Any] = {
        'SYSTEM': {'data_path': './data/EURUSD_2022-2024.csv', 'main_timeframe': 'H1', 'execution_timeframe': 'M5', 'use_multiprocessing': True, 'num_processes': 4}, 
        'TIME_RANGE': {'start_date': '2023-01-01', 'end_date': '2024-06-30', 'session_start_time': '09:00', 'session_end_time': '17:00'}
    }

    # fucntion call
    result_df = get_data_df(user_settings)

    # Check the df is in the correct form 
    assert result_df.index.name == 'time'

    # Cehck we have the right columns
    expected_cols = ['open', 'high', 'low', 'close']
    assert list(result_df.columns) == expected_cols



    pass