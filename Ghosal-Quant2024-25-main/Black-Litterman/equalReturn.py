import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

def black_litterman(
    Sigma,
    w_mkt,
    P,
    Q,
    tau=0.05,
    delta=2.5,
    Omega=None
):
    """
    Compute the Black-Litterman expected returns and optimal portfolio weights.

    Parameters:
    - Sigma: Covariance matrix of asset returns (N x N)
    - w_mkt: Market capitalization weights (N x 1)
    - P: Pick matrix for the views (K x N)
    - Q: Expected returns for the views (K x 1)
    - tau: Scalar, typically small (e.g., 0.05)
    - delta: Risk aversion coefficient
    - Omega: Covariance matrix of view errors (K x K)

    Returns:
    - w_bl: Optimal portfolio weights (N x 1)
    - mu_bl: Posterior expected returns (N x 1)
    """

    # Equilibrium returns (implied returns)
    pi = delta * Sigma @ w_mkt

    # If Omega is not provided, compute it using the established method
    if Omega is None:
        Omega = tau * P @ Sigma @ P.T

    # Compute the posterior expected returns
    M = np.linalg.inv(np.linalg.inv(tau * Sigma) + P.T @ np.linalg.inv(Omega) @ P)
    mu_bl = M @ (np.linalg.inv(tau * Sigma) @ pi + P.T @ np.linalg.inv(Omega) @ Q)

    # Compute the optimal portfolio weights
    w_bl = (1 / delta) * np.linalg.inv(Sigma) @ mu_bl

    return w_bl, mu_bl

def fetch_data(stocks, start_date, end_date):
    # Fetch adjusted closing prices
    data = yf.download(stocks, start=start_date, end=end_date)['Adj Close']
    data = data.dropna()

    # Compute daily returns
    returns = data.pct_change().dropna()

    return data, returns

def get_market_caps(stocks):
    tickers = yf.Tickers(stocks)
    market_caps = []
    for stock in stocks:
        market_cap = tickers.tickers[stock].info.get('marketCap', None)
        if market_cap is not None:
            market_caps.append(market_cap)
        else:
            raise ValueError(f"Market cap for {stock} could not be fetched.")
    return np.array(market_caps)

def main():
    # List of your stocks
    stocks = ['TPR', 'RWE.DE', 'IMI.L', 'RIO', 'CALM', 'GOOGL']

    # Define the date range
    start_date = '2022-01-01'
    end_date = datetime.today().strftime('%Y-%m-%d')

    # Fetch data
    data, returns = fetch_data(stocks, start_date, end_date)

    # Compute the covariance matrix (Sigma)
    Sigma = returns.cov().values

    # Fetch market capitalizations using Yahoo Finance
    market_caps = get_market_caps(stocks)

    # Compute market weights
    w_mkt = market_caps / market_caps.sum()

    # Number of assets
    N = len(stocks)

    # Step 3: Define investor absolute views
    # Since you have an expected return of 9% for each stock, we'll set up the P matrix as an identity matrix
    # and Q vector with 9% for each stock

    # Create the P matrix as an identity matrix
    P = np.eye(N)

    # Expected returns for the views (Q)
    # Convert 9% to decimal
    Q = np.full(N, 0.09)

    # Risk aversion coefficient (delta)
    delta = 2.5  # Adjust based on your risk tolerance

    # Scalar tau
    tau = 0.05  # Adjust based on your confidence in the market equilibrium

    # Compute the Black-Litterman portfolio weights and expected returns
    w_bl, mu_bl = black_litterman(Sigma, w_mkt, P, Q, tau, delta)

    # Normalize weights to sum to 1
    w_bl = w_bl / np.sum(w_bl)

    # Display the results
    print("\nOptimal Portfolio Weights (Black-Litterman):")
    for stock, weight in zip(stocks, w_bl):
        print(f"{stock}: {weight:.4f}")

    print("\nPosterior Expected Returns (Black-Litterman):")
    for stock, mu in zip(stocks, mu_bl):
        print(f"{stock}: {mu:.4f}")
    # Display the results in percentages
    print("\nOptimal Portfolio Weights (Black-Litterman):")
    for stock, weight in zip(stocks, w_bl):
        print(f"{stock}: {weight * 100:.2f}%")

    print("\nPosterior Expected Returns (Black-Litterman):")
    for stock, mu in zip(stocks, mu_bl):
        print(f"{stock}: {mu * 100:.2f}%")
       

    # Calculate portfolio daily returns for the Black-Litterman portfolio
    portfolio_returns_bl = returns.dot(w_bl)

    # Calculate portfolio daily returns for the equal-weighted portfolio
    equal_weights = np.full(len(stocks), 1 / len(stocks))
    portfolio_returns_equal = returns.dot(equal_weights)

    # Compute cumulative returns
    cumulative_returns_bl = (1 + portfolio_returns_bl).cumprod()
    cumulative_returns_equal = (1 + portfolio_returns_equal).cumprod()

    # Plot cumulative returns
    plt.figure(figsize=(10, 6))
    plt.plot(cumulative_returns_bl, label='Black-Litterman Portfolio')
    plt.plot(cumulative_returns_equal, label='Equal-Weighted Portfolio')
    plt.title('Cumulative Returns')
    plt.xlabel('Date')
    plt.ylabel('Cumulative Return')
    plt.legend()
    plt.show()

if __name__ == "__main__":
    main()
