import yfinance as yf
import pandas as pd
import seaborn as sns
import matplotlib.pyplot as plt

# Define the tickers for Garmin and its competitors
tickers = ["GRMN", "HON", "RTX", "TDY", "BC", "TOM2.AS", "GPRO", "ST"]  # TomTom uses Amsterdam stock exchange ticker

# Set the date range (3 years back until the end of February 2024)
start_date = "2021-02-28"
end_date = "2024-02-29"

def get_price_data(tickers, start_date, end_date):
    """
    Fetches historical price data for the given tickers from Yahoo Finance.
    
    Parameters:
        tickers (list of str): List of stock tickers to fetch data for.
        start_date (str): Start date for the data in 'YYYY-MM-DD' format.
        end_date (str): End date for the data in 'YYYY-MM-DD' format.
    
    Returns:
        pd.DataFrame: DataFrame containing adjusted close prices for each ticker.
    """
    # Fetch data for each ticker
    data = yf.download(tickers, start=start_date, end=end_date, interval="1d")["Adj Close"]
    
    # Check for missing data
    print("Missing data count per ticker:")
    print(data.isnull().sum())
    
    # Drop rows with missing values
    data = data.dropna()
    
    return data

# Fetch the adjusted close prices
price_data = get_price_data(tickers, start_date, end_date)

# Calculate daily returns and check if they look correct
returns = price_data.pct_change().dropna()
print("Sample of calculated returns:")
print(returns.head())

# Calculate the correlation matrix of returns
correlation_matrix = returns.corr()

# Plot the correlation matrix with maximum visibility adjustments and bold labels
plt.figure(figsize=(20, 18))  # Increased figure size for more screen space
sns.heatmap(correlation_matrix, annot=True, fmt=".2f", cmap="coolwarm", cbar=True,
            annot_kws={"size": 30},  # Larger font size for annotations
            xticklabels=tickers, yticklabels=tickers)
plt.xticks(fontsize=24, rotation=45, fontweight="bold")  # Larger, bold font for x-axis labels
plt.yticks(fontsize=24, fontweight="bold")  # Larger, bold font for y-axis labels
plt.show()


