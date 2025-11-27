import pandas as pd

from backtester import Backtester

import sys, os, re
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from strategies.test_strategy import example_strategy


# GPT code 
def create_test_dataframe(granularity: str) -> pd.DataFrame:
    """
    Creates a test DataFrame with dynamic timestamps based on the provided granularity.
    
    Args:
        granularity (str): e.g., 'M5' (5 mins), 'S30' (30 secs), 'H1' (1 hour), 'D1' (1 Day)
    """
    
    # 1. Separate the Price data (Open, High, Low, Close)
    # We remove the hardcoded Date/Time strings from here
    price_data = [
        [100.00, 101.10, 100.00, 100.80],
        [100.80, 101.50, 100.70, 101.20],
        [101.20, 101.40, 100.80, 100.90],
        [100.90, 101.60, 100.80, 101.50],
        [101.50, 102.40, 101.30, 102.00],
        [102.00, 102.90, 101.90, 102.50],
        [102.50, 103.10, 102.30, 102.80],
        [105.00, 103.10, 102.30, 102.80], # TP Trigger candle
    ]

    # 2. Parse Granularity to Pandas Frequency
    # Assumes format: [Letter][Number] -> M5, H1, S30
    match = re.match(r"([A-Za-z]+)(\d+)", granularity)
    
    if match:
        unit, value = match.groups()
        # Map common trading letters to Pandas offsets
        # M -> min (minutes), H -> h (hours), S -> s (seconds), D -> D (days)
        mapping = {'M': 'min', 'H': 'h', 'S': 's', 'D': 'D', 'W': 'W'}
        
        if unit.upper() in mapping:
            freq = f"{value}{mapping[unit.upper()]}"
        else:
            # Fallback if specific unit not found
            freq = f"{value}{unit}"
    else:
        # If input is already like '5min' or just 'D'
        freq = granularity

    # 3. Generate the Time Range
    start_time = pd.Timestamp('2025-11-15 09:00:00')
    time_index = pd.date_range(start=start_time, periods=len(price_data), freq=freq)

    # 4. Construct the DataFrame
    df = pd.DataFrame(price_data, columns=['Open', 'High', 'Low', 'Close'])

    # 5. Insert Date and Time columns to match your original schema
    # Note: It is usually better to have one DateTime object column, 
    # but we are sticking to your requested format (Date str, Time str).
    df.insert(0, 'Time', time_index.strftime('%H:%M:%S'))
    df.insert(0, 'Date', time_index.strftime('%Y-%m-%d'))

    return df


def test_prepare_data():

    # define a dataframe - according to expected data 
    test_df_m5: pd.DataFrame = create_test_dataframe("M5")
    test_df_S30: pd.DataFrame = create_test_dataframe("M5")

    # instantiate Backtester object - mimic the run() function 
    backtest_obj: Backtester = Backtester(test_df_m5, test_df_S30, example_strategy)

    # Should leavbe backtester.df with an extra buy/sell ilumn 
    backtest_obj.prepare_data()

    # Check that the columns exist and are initialized to 0
    # We dont have these signals on the lower granularity df 
    assert 'BUY' in backtest_obj.df_hour_data.columns
    assert 'SELL' in backtest_obj.df_hour_data.columns
    # Assert that ALL values in the new columns are 0
    assert (backtest_obj.df_hour_data["BUY"] == 0).all()
    assert (backtest_obj.df_hour_data["SELL"] == 0).all()


def test_apply_strategy():

    # define a dataframe - according to expected data 
    test_df_m5: pd.DataFrame = create_test_dataframe("M5")
    test_df_s30: pd.DataFrame = create_test_dataframe("S30")

    # instantiate Backtester object - mimic the run() function 
    backtest_obj: Backtester = Backtester(test_df_m5, test_df_s30, example_strategy)

    # Should leavbe backtester.df with an extra buy/sell col
    # Can assume this works by the above test....
    backtest_obj.prepare_data()

    # Assert the buy and sells exist in the updated df 

    # Manually calculated expected signals (based on Close price comparison):
    # Index 0: NaN (No previous close) -> 0
    # Index 1: 101.20 > 100.80 -> +1 (Buy)
    # Index 2: 100.90 < 101.20 -> -1 (Sell)
    # Index 3: 101.50 > 100.90 -> +1 (Buy)
    # Index 4: 102.00 > 101.50 -> +1 (Buy)
    # Index 5: 102.50 > 102.00 -> +1 (Buy)
    # Index 6: 102.80 > 102.50 -> +1 (Buy)

    # Apply strategy
    backtest_obj.apply_strategy()

    # Print the df 
    # print(f"{backtest_obj.df=}")
    
    # Check if the 'SIGNAL' column was created
    assert 'SIGNAL' in backtest_obj.df_hour_data.columns
    
    expected_signals = pd.Series([0, 1, -1, 1, 1, 1, 1, 0], name='SIGNAL')
    # Check if the generated 'SIGNAL' column matches the expected Series
    # Use numpy.testing.assert_array_equal for robust pandas Series comparison
    pd.testing.assert_series_equal(backtest_obj.df_hour_data['SIGNAL'], expected_signals)
    

def test_run_backtest():
    """
    Test the use case of the Backtester class 
    """

    # define a dataframe - according to expected data 
    test_df: pd.DataFrame = create_test_dataframe()

    # instantiate Backtester object - mimic the run() function 
    backtest_obj: Backtester = Backtester(test_df, example_strategy)

    # Should leavbe backtester.df with an extra buy/sell col
    # Can assume this works by the above test....
    backtest_obj.prepare_data()

    # Assert the buy and sells exist in the updated df 

    # Manually calculated expected signals (based on Close price comparison):
    # Index 0: NaN (No previous close) -> 0
    # Index 1: 101.20 > 100.80 -> +1 (Buy)
    # Index 2: 100.90 < 101.20 -> -1 (Sell)
    # Index 3: 101.50 > 100.90 -> +1 (Buy)
    # Index 4: 102.00 > 101.50 -> +1 (Buy)
    # Index 5: 102.50 > 102.00 -> +1 (Buy)
    # Index 6: 102.80 > 102.50 -> +1 (Buy)

    # Apply strategy
    backtest_obj.apply_strategy()

    # Run the backtest
    backtest_obj.run_backtest()

    # print(f"{backtest_obj.df_results=}")

    # check for the df_results
    # backtest_obj.df_results