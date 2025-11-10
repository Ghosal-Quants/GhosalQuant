import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime
import cvxpy as cp
import matplotlib.pyplot as plt
import time
from sklearn.covariance import LedoitWolf
import statsmodels.api as sm
from statsmodels.robust.robust_linear_model import RLM
from scipy.stats import zscore


delta = 1.0

def black_litterman(Sigma, w_mkt, P, Q, tau=0.05, delta=0.3, Omega=None):
    # Calculate equilibrium returns (implied returns)
    pi = delta * Sigma @ w_mkt
    # If Omega is not provided, calculate it based on uncertainty in views
    if Omega is None:
        # Use a variance-scaled Omega to better reflect uncertainty in views
        Omega = np.diag(np.diag(P @ Sigma @ P.T)) * tau
    # Compute the posterior expected returns (mu_bl)
    mu_bl = np.linalg.inv(np.linalg.inv(tau * Sigma) + P.T @ np.linalg.inv(Omega) @ P) @ \
            (np.linalg.inv(tau * Sigma) @ pi + P.T @ np.linalg.inv(Omega) @ Q)
    # Compute the posterior covariance matrix (Sigma_bl)
    Sigma_bl = Sigma + np.linalg.inv(np.linalg.inv(tau * Sigma) + P.T @ np.linalg.inv(Omega) @ P)
    return mu_bl, Sigma_bl


def fetch_data(stocks, start_date, end_date, retries=3):
    for attempt in range(retries):
        try:
            data = yf.download(stocks, start=start_date, end=end_date)['Adj Close']
            if data.empty:
                raise ValueError("Downloaded data is empty.")
            data = data.dropna()  # Drop rows with missing data
            returns = data.pct_change().dropna()  # Calculate daily returns
            return data, returns
        except Exception as e:
            if attempt < retries - 1:
                print(f"Attempt {attempt + 1} failed to download data. Retrying...")
                time.sleep(3)  # Wait before retrying
            else:
                raise ValueError(f"Unable to download data after {retries} attempts. Error: {e}")


def fetch_stock_currency(stocks):
    stock_currency = {}
    for stock in stocks:
        try:
            ticker = yf.Ticker(stock)
            stock_currency[stock] = ticker.info.get('currency', 'USD')
        except Exception as e:
            print(f"Error fetching currency for {stock}: {e}")
    return stock_currency


def fetch_market_caps(stocks):
    market_caps = []
    for stock in stocks:
        try:
            ticker = yf.Ticker(stock)
            market_cap = ticker.info.get('marketCap', None)
            if market_cap is None:
                print(f"Market cap not available for {stock}. Adding NaN.")
                market_caps.append(np.nan)
                continue
            market_caps.append(float(market_cap))  # Ensure float type
        except Exception as e:
            print(f"Error fetching market cap for {stock}: {e}. Adding NaN.")
            market_caps.append(np.nan)
    return np.array(market_caps, dtype=np.float64)


def checkAndConvertForGbp(data, stock_currency):
    converted_data = data.copy()
    for stock, currency in zip(data.columns, stock_currency):
        if currency == 'GBp':
            converted_data[stock] /= 100
    return converted_data



def train_factor_model(stocks):
    momentum, size, value, forward_return = [], [], [], []

    for stock in stocks:
        try:
            ticker = yf.Ticker(stock)
            hist = ticker.history(period="max")
            n = len(hist)
            
            if n < 252:
                continue

            for i in range(n - 504):
                past_index = i + 252
                forward_index = i + 504

                if past_index >= n or forward_index >= n:
                    break

                past_return = (hist['Close'].iloc[past_index] - hist['Close'].iloc[i]) / hist['Close'].iloc[i]
                forward = (hist['Close'].iloc[forward_index] - hist['Close'].iloc[past_index]) / hist['Close'].iloc[past_index]
                
                momentum.append(past_return)
                forward_return.append(forward)
                
                market_cap = ticker.info.get('marketCap', np.nan)
                size.append(np.log1p(market_cap) if market_cap else np.nan)
                
                pe_ratio = ticker.info.get('trailingPE', np.nan)
                value.append(np.log1p(pe_ratio))

        except Exception as e:
            print(f"Error fetching data for {stock}: {e}")
            continue

    # Create DataFrame
    data = pd.DataFrame({
        'Momentum': momentum,
        'Size': size,
        'Value': value,
        'Forward_Return': forward_return
    }).dropna()

    # Winsorize to reduce extreme outliers
    data['Momentum'] = data['Momentum'].clip(lower=data['Momentum'].quantile(0.01), upper=data['Momentum'].quantile(0.99))
    data['Size'] = data['Size'].clip(lower=data['Size'].quantile(0.01), upper=data['Size'].quantile(0.99))
    data['Value'] = data['Value'].clip(lower=data['Value'].quantile(0.01), upper=data['Value'].quantile(0.99))

    # Standardize data (Z-score normalization)
    X = data[['Momentum', 'Size', 'Value']].apply(zscore)
    y = data['Forward_Return']

    # Perform Robust Regression (RLM)
    robust_model = RLM(y, sm.add_constant(X)).fit()
    print(robust_model.summary())
    
    return robust_model.params['Momentum'], robust_model.params['Size'], robust_model.params['Value']



def calculate_expected_returns(dcf_inputs, a, b, c, risk_free_rate=0.03):
    """
    Calculate expected returns for specific stocks using trained factor loadings.
    The risk-free rate is externally set and combined with factor returns.
    """
    Q = []
    
    for inputs in dcf_inputs:
        stock = inputs['stock']
        current_price = inputs['current_price']
        intrinsic_value = inputs['intrinsic_value_per_share']
        
        # DCF-based expected return
        expected_return_dcf = (intrinsic_value - current_price) / current_price
        
        try:
            ticker = yf.Ticker(stock)
            hist = ticker.history(period="1y")
            
            # Calculate momentum (last 12 months return)
            momentum_stock = (hist['Close'][-1] - hist['Close'][0]) / hist['Close'][0]
            
            # Get size and value factors
            market_cap_stock = ticker.info.get('marketCap', np.nan)
            size_stock = np.log(market_cap_stock) if market_cap_stock else np.nan
            pe_ratio_stock = ticker.info.get('trailingPE', np.nan)
            
            # Calculate regression-based expected return (Risk-Free Rate + Factors)
            expected_return_regression = (
                risk_free_rate + a * momentum_stock + b * size_stock + c * pe_ratio_stock
            )
            
            # Weighted expected return (50% DCF, 50% Regression)
            weighted_expected_return = 0.5 * expected_return_dcf + 0.5 * expected_return_regression
            
            Q.append(weighted_expected_return)
            print(f"{stock} - Expected Return: {weighted_expected_return * 100:.2f}%")
        
        except Exception as e:
            print(f"Error calculating return for {stock}: {e}")
            Q.append(np.nan)
    
    return np.array(Q)


def monte_carlo_simulation(returns, w_bl, num_days=252, num_simulations=1000):
    """
    Simulates portfolio paths using log returns draws from multivariate normal
    """
    log_mean_returns = returns.mean().values  # Mean of log returns
    log_cov_matrix = returns.cov().values     # Covariance matrix of log returns

    # Simulate multi-asset log returns, account for structure of covariance of the assets 
    simulated_log_returns = np.random.multivariate_normal(log_mean_returns, log_cov_matrix, (num_days, num_simulations))

    # Take linear combo of paths to recover full portfolio movement
    portfolio_log_returns = np.dot(simulated_log_returns, w_bl)

    # exp to get back to actual returns 
    simulated_cumulative_returns = np.exp(np.cumsum(portfolio_log_returns, axis=0))
    return simulated_cumulative_returns


def main():
    # Define start and end dates
    start_date = '2022-01-01'
    end_date = datetime.today().strftime('%Y-%m-%d')

    # Define list of stocks
    stocks = ['TPR', 'RWE.DE', 'IMI.L', 'RIO', 'CALM', 'GOOGL']

    # Fetch stock currencies
    stock_currency = fetch_stock_currency(stocks)

    # Fetch and convert market caps
    market_caps = fetch_market_caps(stocks)

    # Fetch historical data
    try:
        data, returns = fetch_data(stocks, start_date, end_date)
    except ValueError as e:
        print(f"Error fetching data: {e}")
        return

    if data.empty:
        raise ValueError("No data available for the given stocks and date range.")

    # Convert prices to the base currency
    data_cleaned = checkAndConvertForGbp(data, [stock_currency[s] for s in stocks])

    current_prices = data_cleaned.iloc[-1].to_dict()

    # Calculate market capitalizations
    market_caps = market_caps

    # Calculate market weights
    w_mkt = market_caps / np.nansum(market_caps)

    # Calculate the covariance matrix using Ledoit-Wolf shrinkage for robustness
    lw = LedoitWolf()
    Sigma = lw.fit(returns).covariance_

    # Define number of assets
    N = len(stocks)

    # Define DCF inputs for each stock
    dcf_inputs = [
        {'stock': 'TPR', 'current_price': current_prices['TPR'], 'intrinsic_value_per_share': 65.59},
        {'stock': 'RWE.DE', 'current_price': current_prices['RWE.DE'], 'intrinsic_value_per_share': 42.85},
        {'stock': 'IMI.L', 'current_price': current_prices['IMI.L'], 'intrinsic_value_per_share': 19.52},
        {'stock': 'RIO', 'current_price': current_prices['RIO'], 'intrinsic_value_per_share': 70.09},
        {'stock': 'CALM', 'current_price': current_prices['CALM'], 'intrinsic_value_per_share': 140.16},
        {'stock': 'GOOGL', 'current_price': current_prices['GOOGL'], 'intrinsic_value_per_share': 196.87}
    ]
    

    # Train linear regression
    large_stock_list = [
    # Technology
    'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'ORCL', 'AMD', 'ADI', 'TXN', 'CRM', 
    'IBM', 'QCOM', 'INTC', 'CSCO', 'MU', 'PYPL', 'AVGO', 'NFLX', 'NOW',
    
    # Consumer Staples
    'PG', 'KO', 'PEP', 'WMT', 'COST', 'MO', 'PM', 'CL', 'KMB', 'GIS', 'SYY', 
    'HSY', 'MDLZ', 'K', 'KR', 'CPB',

    # Consumer Discretionary
    'HD', 'DIS', 'NKE', 'TGT', 'LOW', 'TJX', 'MCD', 'SBUX', 'ROST', 'DHI', 
    'MAR', 'BKNG', 'LULU', 'AZO', 'DG',

    # Materials
    'DOW', 'DD', 'NEM', 'APD', 'LIN', 'SHW', 'FCX', 'BLL', 'IP', 'VMC', 
    'ECL', 'CE', 'MOS', 'PKG', 'PPG',

    # Energy
    'XOM', 'CVX', 'COP', 'EOG', 'SLB', 'HAL', 'OXY', 'PSX', 'VLO', 'MRO', 
    'MPC', 'PXD', 'FANG', 'WMB', 'KMI',

    # Industrials
    'CAT', 'BA', 'MMM', 'HON', 'GE', 'RTX', 'UPS', 'LMT', 'UNP', 'GD', 
    'DE', 'EMR', 'ITW', 'CSX', 'NSC', 'ETN', 'FDX', 'CMI', 'PCAR', 'ROK'
]
    
    a, b, c = train_factor_model(large_stock_list)


    # Calculate expected returns using the new function
    Q = calculate_expected_returns(dcf_inputs, a, b, c, risk_free_rate=0.0468)  # 4% gilt rate

    # Define P matrix for views
    P = np.eye(N)

    # Define tau
    tau = 0.05

    # Calculate posterior expected returns and covariance matrix
    mu_bl, Sigma_bl = black_litterman(Sigma, w_mkt, P, Q, tau, delta)

    risk_aversion = delta

    # Set up optimization problem using cvxpy
    w = cp.Variable(N)
    risk_aversion = delta
    lambda_reg = 0.0005  # Further reduce regularization parameter to encourage diversification
    diversification_penalty = cp.sum(cp.entr(w + 1e-8))  # Entropy-like term to encourage diversification
    risk_adjusted_return_objective = mu_bl @ w - risk_aversion * cp.quad_form(w, Sigma_bl)  # Linear risk-adjusted return objective
    objective = cp.Maximize(risk_adjusted_return_objective + 0.02 * diversification_penalty)  # Increase diversification penalty weight
    
    # Sector constraints - treat each stock as an individual sector
    sector_constraints = [
        w[i] <= 0.4 for i in range(N)  # Set an upper bound to prevent any single stock from dominating the portfolio
    ]
    constraints = [
        cp.sum(w) == 1,
        w >= 0.10  # Lower minimum weight to allow more flexibility in allocation
    ] + sector_constraints
    problem = cp.Problem(objective, constraints)
    problem.solve()
    

    # Get the raw optimal weights from the BL optimization
    w_bl = w.value

    # Print optimal portfolio weights
    print("\nOptimal Portfolio Weights (Black-Litterman with Constraints):")
    for stock, weight in zip(stocks, w_bl):
        print(f"{stock}: {weight * 100:.2f}%")

    # 2. Apply Exponential Scaling to Amplify Differences
    exp_weights = np.exp(w_bl)  # Exponentiate to amplify differences
    scaled_weights = exp_weights / np.sum(exp_weights)  # Re-normalize to sum to 1

    # 3. Rescale to Fit 3% to 5% Range (Smooth Distribution)
    w_min = 0.03  # Minimum weight (3%)
    w_max = 0.05  # Maximum weight (5%)

    # Linear interpolation for rescaled weights
    rescaled_weights = w_min + (w_max - w_min) * ((scaled_weights - np.min(scaled_weights)) /
                                                  (np.max(scaled_weights) - np.min(scaled_weights)))

    # 4. Calculate Cash Allocation
    total_rescaled_weight = np.sum(rescaled_weights)
    cash_weight = 1 - total_rescaled_weight  # Remainder goes to cash

    # Print the results
    print("\nOptimal Portfolio Weights (Smooth 3-5% with Cash):")
    for stock, weight in zip(stocks, rescaled_weights):
        print(f"{stock}: {weight * 100:.2f}%")
    print(f"Cash: {cash_weight * 100:.2f}%")

    # Visualization
    plt.figure(figsize=(8, 8))
    plt.pie(np.append(rescaled_weights, cash_weight), labels=stocks + ['Cash'], autopct='%1.1f%%', startangle=140)
    plt.title('Black-Litterman Portfolio Allocation (Smooth 3-5% with Cash)')
    plt.show()


    # Monte Carlo
    simulated_cumulative_returns = monte_carlo_simulation(returns, w_bl)

    # Plot simulation results
    plt.figure(figsize=(10, 6))
    plt.plot(simulated_cumulative_returns, alpha=0.5)
    plt.title('Monte Carlo Simulation of Portfolio Returns')
    plt.xlabel('Days')
    plt.ylabel('Cumulative Return')
    plt.show()

    # Calculate and print descriptive statistics of simulated returns
    mean_return = np.mean(simulated_cumulative_returns)
    std_dev = np.std(simulated_cumulative_returns)
    percentile_5th = np.percentile(simulated_cumulative_returns, 5)
    percentile_95th = np.percentile(simulated_cumulative_returns, 95)

    print(f"Mean Return: {mean_return:.4f}")
    print(f"Standard Deviation: {std_dev:.4f}")
    print(f"5th Percentile: {percentile_5th:.4f}")
    print(f"95th Percentile: {percentile_95th:.4f}")

    # Plot a histogram of simulated returns
    plt.hist(simulated_cumulative_returns, bins=50)  # Adjust bins as needed
    plt.title("Distribution of Simulated Portfolio Returns")
    plt.xlabel("Portfolio Return")
    plt.ylabel("Frequency")
    plt.show()

    # Plot the CDF of simulated returns
    sorted_returns = np.sort(simulated_cumulative_returns)
    cdf_values = np.arange(1, len(sorted_returns) + 1) / len(sorted_returns)
    plt.plot(sorted_returns, cdf_values)
    plt.title("Cumulative Distribution Function of Simulated Returns")
    plt.xlabel("Portfolio Return")
    plt.ylabel("Cumulative Probability")
    plt.grid(True)
    plt.show()


if __name__ == "__main__":
    main()
