import pandas as pd

from util.deprecation import deprecated

@deprecated("example_strategy is legacy code. Please use a Strategy class instead.")
def example_strategy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies a simple test strategy with defined Stop Loss (SL) and Take Profit (TP).

    BUY signal (1) if Close > Previous Close.
    SELL signal (-1) if Close < Previous Close.
    Otherwise, signal is 0.

    SL is fixed at 1.0% and TP at 2.0% of the entry price.
    """

    # 1. Generate the simple BUY/SELL signals
    # ======================================

    # Calculate the change in price
    df['Price_Change'] = df['Close'].diff()

    # Create a unified SIGNAL column (1 for Buy, -1 for Sell, 0 for None)
    df['SIGNAL'] = 0 # Default to no trade
    df.loc[df['Price_Change'] > 0, 'SIGNAL'] = 1 # BUY (Long)
    df.loc[df['Price_Change'] < 0, 'SIGNAL'] = -1 # SELL (Short)

    # 2. Define the SL/TP Prices
    # ==========================

    # Define the fixed percentage values
    SL_PERCENT = 0.010  # 1.0% Stop Loss
    TP_PERCENT = 0.020  # 2.0% Take Profit (1:2 R:R Ratio)

    # Initialize SL and TP columns
    df['SL'] = 0.0
    df['TP'] = 0.0

    # --- Logic for BUY (Long) Trades (SIGNAL == 1) ---

    # SL is 1.0% BELOW the entry price (Close)
    buy_sl = df['Close'] * (1 - SL_PERCENT)
    df.loc[df['SIGNAL'] == 1, 'SL'] = buy_sl

    # TP is 2.0% ABOVE the entry price (Close)
    buy_tp = df['Close'] * (1 + TP_PERCENT)
    df.loc[df['SIGNAL'] == 1, 'TP'] = buy_tp

    # --- Logic for SELL (Short) Trades (SIGNAL == -1) ---

    # SL is 1.0% ABOVE the entry price (Close)
    sell_sl = df['Close'] * (1 + SL_PERCENT)
    df.loc[df['SIGNAL'] == -1, 'SL'] = sell_sl

    # TP is 2.0% BELOW the entry price (Close)
    sell_tp = df['Close'] * (1 - TP_PERCENT)
    df.loc[df['SIGNAL'] == -1, 'TP'] = sell_tp

    # 3. Clean up temporary columns
    # =============================
    df.drop(columns=['Price_Change'], inplace=True)

    return df