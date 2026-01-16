"""main controller for listEditor.py and staticUpdater.py
handles calls from strategy class to check retrieve current data with updated data
strategy calls our functions getData with the [tickers] and [tickerType] and [timeframes] 
(one with the execution time frame and one with a main time frame), startDate, endDate
to get the data it needs in two individual parquet dataframes and returns those to the strategy classed that its been called
from.
we only have all the data stored in main.parquet for stocks and options in a 1m timeframe so we will have to
compile the other timeframes from that. 
Main Parquet schema (flattened):
    ticker, t, o, h, l, c, v, n, vw
Where fields mirror Polygon `results` objects:
    c=close, h=high, l=low, n=number of transactions, o=open, t=unix ms, v=volume, vw=vol-weighted avg price
We have no data handler class so its all called from in here
the data is in the same path as this file
"""
import pandas as pd
import os
from datetime import datetime, timedelta
import pytz
import listEditor
import staticUpdater
DATA_PATH = os.path.dirname(os.path.abspath(__file__))
MAIN_DATA_FILE = os.path.join(DATA_PATH, 'data/main.parquet')
FOLLOWING_DATA_FILE = os.path.join(DATA_PATH, 'data/following.parquet')


def resample_data(df, timeframe):
    """resample the data to the requested timeframe
    df: dataframe with 1m data
    timeframe: string of timeframe to resample to (e.g. '1Min', '5Min', '15Min', '1H', '1D')
    returns resampled dataframe
    """
    # set t as index
    df = df.set_index('t')

    # resample
    ohlcv_dict = {
        'o': 'first',
        'h': 'max',
        'l': 'min',
        'c': 'last',
        'v': 'sum',
        'n': 'sum',
        'vw': 'mean'
    }
    resampled_df = df.groupby('ticker').resample(timeframe).agg(ohlcv_dict).dropna().reset_index()

    return resampled_df


def getData(tickers, tickerType, timeframes, startDate, endDate):
    """main function called from strategy classes to get data
    tickers: list of ticker strings
    tickerType: string of ticker type (stock, option, crypto, forex)
    timeframes: list of two timeframes [executionTimeframe, mainTimeframe]
    startDate: datetime object start date
    endDate: datetime object end date
    returns two dataframes with data for the tickers in the timeframes requested
    """
    # check following.parquet to see if we have the tickers stored already but also check if it is option or stock before adding to following
    if tickerType is 'stock':
        if os.path.exists(FOLLOWING_DATA_FILE):
            dF = pd.read_parquet(FOLLOWING_DATA_FILE)
            stored_tickers = dF['ticker'].unique().tolist()
            missing_tickers = [ticker for ticker in tickers if ticker not in stored_tickers]
            if missing_tickers:
                for x in missing_tickers:
                    listEditor.addStock(x)
        else:
            for x in tickers:
                listEditor.addStock(x)
    elif tickerType is 'option':
        if os.path.exists(FOLLOWING_DATA_FILE):
            dF = pd.read_parquet(FOLLOWING_DATA_FILE)
            stored_tickers = dF['ticker'].unique().tolist()
            missing_tickers = [ticker for ticker in tickers if ticker not in stored_tickers]
            if missing_tickers:
                for x in missing_tickers:
                    listEditor.addOption(x)
        else:
            for x in tickers:
                listEditor.addOption(x)
    else:
        pass
    
    
    #run staticUpdate to get newest candle
    staticUpdater.update_main_from_following()

    # load main parquet data
    df = pd.read_parquet(MAIN_DATA_FILE)

    # filter by tickerType if needed (currently only stock and option supported)
    if tickerType not in ['stock', 'option']:
        raise ValueError("Unsupported tickerType. Only 'stock' and 'option' are supported.")

    # filter by tickers
    df = df[df['ticker'].isin(tickers)]

    # convert t to datetime
    df['t'] = pd.to_datetime(df['t'], unit='ms').dt.tz_localize('UTC')

    # filter by date range
    df = df[(df['t'] >= startDate) & (df['t'] <= endDate)]

    # prepare dataframes for each timeframe
    exec_tf, main_tf = timeframes
    exec_df = resample_data(df, exec_tf)
    main_df = resample_data(df, main_tf)

    return exec_df, main_df


# Example usage
if __name__ == "__main__":
    tickers = ['NVDA', 'SPY']
    tickerType = 'stock'
    timeframes = ['1Min', '1D']
    startDate = datetime(2024, 1, 1, tzinfo=pytz.UTC)
    endDate = datetime(2024, 1, 31, tzinfo=pytz.UTC)

    exec_data, main_data = getData(tickers, tickerType, timeframes, startDate, endDate)

    print("Execution Timeframe Data:")
    print(exec_data.head())

    print("\nMain Timeframe Data:")
    print(main_data.head())
