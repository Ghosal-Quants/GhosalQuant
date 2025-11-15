import pandas as pd

# ***Gemini code***
def example_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies a simple test strategy:
    BUY signal (1) if Close > Previous Close.
    SELL signal (-1) if Close < Previous Close.
    Otherwise, signal is 0.
    """
    
    # Calculate the change in price
    df['Price_Change'] = df['Close'].diff()
    
    # Generate the BUY/SELL signals
    # BUY (1) if the price change is positive
    df['BUY'] = (df['Price_Change'] > 0).astype(int)
    
    # SELL (1) if the price change is negative. Using -1 for a clear distinction.
    df['SELL'] = (df['Price_Change'] < 0).astype(int) * -1 
    
    # Create a unified SIGNAL column for the backtester (1 for Buy, -1 for Sell, 0 for None)
    df['SIGNAL'] = df['BUY'] + df['SELL'] 
    
    # Clean up temporary columns
    df.drop(columns=['Price_Change', 'BUY', 'SELL'], inplace=True)
    
    return df