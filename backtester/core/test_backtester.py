from backtester import Backtester
import pandas as pd



def create_test_dataframe() -> pd.DataFrame:
    """
    define df for the sake of testing 
    """

    data_list = [
    # [Date, Time, Open, High, Low, Close]
    ['2025-11-15', '09:00:00', 100.00, 101.10, 100.00, 100.80],
    ['2025-11-15', '09:05:00', 100.80, 101.50, 100.70, 101.20],
    ['2025-11-15', '09:10:00', 101.20, 101.40, 100.80, 100.90],
    ['2025-11-15', '09:15:00', 100.90, 101.60, 100.80, 101.50],
    ['2025-11-15', '09:20:00', 101.50, 102.40, 101.30, 102.00],
    ['2025-11-15', '09:25:00', 102.00, 102.90, 101.90, 102.50],
    ['2025-11-15', '09:30:00', 102.50, 103.10, 102.30, 102.80]
    ]

    # Define the column names
    columns = ['Date', 'Time', 'Open', 'High', 'Low', 'Close']

    # Create && return DataFrame object
    return pd.DataFrame(data_list, columns=columns)

def test_backtester():
    """
    Test the use case of the Backtester class 
    """

    # define a dataframe - according to expected data 
    test_df: pd.DataFrame = create_test_dataframe()

    # instantiate Backtester object - mimic the run() function 
    backtest_obj: Backtester = Backtester(test_df)
    
    # should output an excel file && condleplots?
    backtest_obj.run_backtest()


    # Run some assertionss on the 


def test_prepare_data():

    # define a dataframe - according to expected data 
    test_df: pd.DataFrame = create_test_dataframe()

    # instantiate Backtester object - mimic the run() function 
    backtest_obj: Backtester = Backtester(test_df)

    # Should leavbe backtester.df with an extra buy/sell ilumn 
    backtest_obj.prepare_data()

    # Check that the columns exist and are initialized to 0
    assert 'BUY' in backtest_obj.df.columns
    assert 'SELL' in backtest_obj.df.columns
    # Assert that ALL values in the new columns are 0
    assert (backtest_obj.df["BUY"] == 0).all()
    assert (backtest_obj.df["SELL"] == 0).all()





