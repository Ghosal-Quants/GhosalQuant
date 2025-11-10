import yfinance as yf
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from datetime import datetime
import matplotlib.pyplot as plt
import seaborn as sns
from functools import lru_cache
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import warnings
from concurrent.futures import ThreadPoolExecutor
import hashlib


@dataclass
class ModelParameters:
    mu: float
    sigma: float
    lambda_param: float
    a: float  # mean jump size
    b: float  # jump volatility

    @property
    def as_tuple(self) -> tuple:
        return (self.mu, self.sigma, self.lambda_param, self.a, self.b)

    @property
    def expected_jump_return(self) -> float:
        """Calculate the expected return contribution from jumps"""
        return self.lambda_param * (np.exp(self.a + (self.b**2)/2) - 1)

    @property
    def total_expected_return(self) -> float:
        """Calculate total expected return including both diffusion and jumps"""
        return self.mu + self.expected_jump_return

class OptionsAnalyzer:
    def __init__(self, ticker_symbol: str, cache_timeout: int = 3600):
        self.ticker_symbol = ticker_symbol
        self.cache_timeout = cache_timeout
        self._cached_stock_price: Optional[float] = None
        self._cached_options_data: Optional[pd.DataFrame] = None
        self._cached_parameters: Optional[ModelParameters] = None
        self._last_update_time: Optional[float] = None

    def analyze_strike_distribution(self) -> Dict:
        """Analyze the distribution of strike prices and their implications"""
        options_df, current_price = self.get_options_data()
        params = self.calibrate_model()

        # only care about liquid otm calls
        liquid_calls = options_df[
            (options_df['volume'] > 10) &
            (options_df['strike'] > current_price) &  # OTM calls
            (options_df['daysToExp'].between(30, 90))  # Focus on 1-3 month options
        ].copy()

        # avg strike weighted by volume
        volume_weighted_strike = (
            (liquid_calls['strike'] * liquid_calls['volume']).sum() / 
            liquid_calls['volume'].sum()
        )

        # implied probability of hitting strike price
        liquid_calls['implied_prob'] = liquid_calls.apply(
            lambda row: self._calculate_strike_probability(
                current_price, 
                row['strike'], 
                row['daysToExp']/365, 
                params
            ), 
            axis=1
        )

        high_volume_strikes = liquid_calls.nlargest(5, 'volume')[['strike', 'volume', 'implied_prob']]
        jump_expected_return = params.expected_jump_return
        total_expected_return = params.total_expected_return

        results = {
            'current_price': current_price,
            'volume_weighted_strike': volume_weighted_strike,
            'implied_return_to_vw_strike': (volume_weighted_strike - current_price) / current_price,
            'high_volume_strikes': high_volume_strikes.to_dict('records'),
            'model_parameters': {
                'mu': params.mu,
                'sigma': params.sigma,
                'lambda': params.lambda_param,
                'mean_jump_size': params.a,
                'jump_volatility': params.b
            },
            'return_components': {
                'diffusion_return': params.mu,
                'jump_return': jump_expected_return,
                'total_expected_return': total_expected_return
            }
        }

        self._plot_strike_analysis(liquid_calls, current_price, params)
        
        return results

    def _calculate_strike_probability(self, S: float, K: float, T: float, params: ModelParameters) -> float:
        """Calculate probability of stock price exceeding strike K at time T"""
        nsims = 10000
        nsteps = max(int(T * 252), 1)
        paths = self._simulate_paths(S, params.as_tuple, T, nsims, nsteps)
        return np.mean(paths[:, -1] > K)

    def _plot_strike_analysis(self, calls_df: pd.DataFrame, current_price: float, params: ModelParameters):
        """Create visualization of strike price analysis"""
        plt.figure(figsize=(15, 8))
        
        # strike prices vs. implied probabilities
        plt.subplot(1, 2, 1)
        sns.scatterplot(
            data=calls_df, 
            x='strike', 
            y='implied_prob',
            size='volume',
            sizes=(20, 200),
            alpha=0.6
        )
        plt.axvline(x=current_price, color='r', linestyle='--', label='Current Price')
        plt.title('Strike Prices vs. Implied Probabilities')
        plt.xlabel('Strike Price')
        plt.ylabel('Implied Probability')
        
        # return distribs from jump diffusion model
        plt.subplot(1, 2, 2)
        T = 0.25  # 3 months
        nsims = 10000
        nsteps = max(int(T * 252), 1)
        paths = self._simulate_paths(current_price, params.as_tuple, T, nsims, nsteps)
        returns = (paths[:, -1] - current_price) / current_price
        
        sns.histplot(returns, bins=50, stat='density')
        plt.axvline(x=0, color='r', linestyle='--', label='No Return')
        plt.axvline(x=params.total_expected_return, color='g', linestyle='--', 
                   label='Expected Return')
        plt.title('3-Month Return Distribution')
        plt.xlabel('Return')
        plt.ylabel('Density')
        plt.legend()
        
        plt.tight_layout()
        plt.show()
        
    @property
    @lru_cache(maxsize=1)
    def risk_free_rate(self) -> float:
        """Cached risk-free rate fetch"""
        try:
            treasury = yf.Ticker("^IRX")
            return treasury.info.get('regularMarketPrice', 5.0) / 100
        except:
            return 0.05

    def _cache_key(self, data: str) -> str:
        """Generate cache key for data"""
        return hashlib.md5(f"{self.ticker_symbol}:{data}".encode()).hexdigest()

    def _is_cache_valid(self) -> bool:
        """Check if cached data is still valid"""
        if self._last_update_time is None:
            return False
        return (datetime.now().timestamp() - self._last_update_time) < self.cache_timeout

    def get_options_data(self) -> Tuple[pd.DataFrame, float]:
        """Get options data with caching"""
        print("Fetching options data...")
        if self._is_cache_valid():
            return self._cached_options_data, self._cached_stock_price

        ticker = yf.Ticker(self.ticker_symbol)
        current_price = ticker.history(period='1d')['Close'].iloc[-1]
        
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for exp in ticker.options:
                futures.append(executor.submit(self._fetch_option_chain, ticker, exp))
            
            all_options = []
            for future in futures:
                try:
                    result = future.result()
                    if result is not None:
                        all_options.append(result)
                except Exception as e:
                    warnings.warn(f"Error fetching options: {e}")

        if not all_options:
            raise ValueError("No valid options data available")

        options_df = pd.concat(all_options, ignore_index=True)
        self._process_options_data(options_df, current_price)
        
        self._cached_options_data = options_df
        self._cached_stock_price = current_price
        self._last_update_time = datetime.now().timestamp()
        
        return options_df, current_price

    def _fetch_option_chain(self, ticker, expiration: str) -> Optional[pd.DataFrame]:
        """Fetch single option chain with error handling"""
        try:
            opt = ticker.option_chain(expiration)
            calls = opt.calls.copy()
            calls['expiration'] = expiration
            return calls
        except:
            return None

    def _process_options_data(self, options_df: pd.DataFrame, current_price: float) -> None:
        """Process options dataframe with calculations"""
        print("Processing options data...")
        options_df['expiration'] = pd.to_datetime(options_df['expiration'])
        options_df['daysToExp'] = (options_df['expiration'] - pd.Timestamp.now()).dt.days
        options_df['moneyness'] = options_df['strike'] / current_price
        
        if 'impliedVolatility' not in options_df.columns:
            options_df['impliedVolatility'] = self._calculate_implied_volatility(options_df, current_price)

    @staticmethod
    @lru_cache(maxsize=1000)
    def _calculate_theoretical_price(params: Tuple[float, ...], S: float, K: float, T: float, r: float) -> float:
        """Cached theoretical price calculation"""
        # print("Calculating theoretical price...")
        mu, sigma, Lambda, a, b = params
        
        if any(np.isnan([mu, sigma, Lambda, a, b])):
            return float('inf')

        nsims = 100
        nsteps = max(int(T * 252), 1)
        # print(f"Simulating {nsims} paths for {nsteps} steps")
        paths = OptionsAnalyzer._simulate_paths(S, params, T, nsims, nsteps)
        
        payoffs = np.maximum(paths[:, -1] - K, 0)
        return np.exp(-r*T) * np.mean(payoffs)

    @staticmethod
    @lru_cache(maxsize=100)
    def _simulate_paths(S: float, params: Tuple[float, ...], T: float, nsims: int, nsteps: int) -> np.ndarray:
        """Cached path simulation"""
        mu, sigma, Lambda, a, b = params
        dt = T / nsteps
        
        # vectors for performance - believe me, messy but faster
        paths = np.zeros((nsims, nsteps + 1))
        paths[:, 0] = S
        
        Z1 = np.random.normal(size=(nsims, nsteps))
        Z2 = np.random.normal(size=(nsims, nsteps))
        poisson = np.random.poisson(Lambda*dt, size=(nsims, nsteps))
        
        # simulate paths
        for i in range(nsteps):
            paths[:, i+1] = paths[:, i] * np.exp(
                (mu - sigma**2/2)*dt +
                sigma*np.sqrt(dt)*Z1[:, i] +
                a*poisson[:, i] +
                np.sqrt(b**2)*np.sqrt(poisson[:, i])*Z2[:, i]
            )
        
        return paths

    def calibrate_model(self) -> ModelParameters:
        print("Calibrating model...")
        """Calibrate model with caching"""
        if self._is_cache_valid() and self._cached_parameters is not None:
            return self._cached_parameters

        options_df, current_price = self.get_options_data()
        
        liquid_options = options_df[
            (options_df['volume'] > 10) & 
            (options_df['daysToExp'].between(5, 365)) &
            (options_df['lastPrice'] > 0.1)
        ].copy()

        if len(liquid_options) < 5:
            raise ValueError("Insufficient liquid options for calibration")

        initial_guess = [0.08, 0.3, 0.1, -0.1, 0.2]
        bounds = [(0.0, 0.5), (0.1, 1.0), (0.0, 1.0), (-0.5, 0.5), (0.0, 0.5)]

        result = minimize(
            lambda params: self._objective_function(params, liquid_options, current_price),
            initial_guess,
            bounds=bounds,
            method='L-BFGS-B'
        )

        if not result.success:
            raise ValueError(f"Optimization failed: {result.message}")

        self._cached_parameters = ModelParameters(*result.x)
        return self._cached_parameters

    def _objective_function(self, params: List[float], options: pd.DataFrame, current_price: float) -> float:
        """Optimized objective function for calibration"""
        theoretical_prices = np.array([
            self._calculate_theoretical_price(
                tuple(params), current_price, row['strike'],
                row['daysToExp']/365, self.risk_free_rate
            )
            for _, row in options.iterrows()
        ])
        
        market_prices = options['lastPrice'].values
        return np.mean((theoretical_prices - market_prices)**2)

    def analyze_price_expectations(self, plot: bool = True) -> Dict:
        """Analyze price expectations with caching"""
        print("Analyzing price expectations...")
        params = self.calibrate_model()
        current_price = self._cached_stock_price
        
        # Generate price distribution
        nsims = 10000
        T = 1.0
        paths = self._simulate_paths(current_price, params.as_tuple, T, nsims, int(T * 252))
        
        final_prices = paths[:, -1]
        results = {
            'current_price': current_price,
            'expected_price': float(np.mean(final_prices)),
            'upside_probability': float(np.mean(final_prices > current_price)),
            'price_95th_percentile': float(np.percentile(final_prices, 95)),
            'price_5th_percentile': float(np.percentile(final_prices, 5)),
            'implied_parameters': {
                'mu': params.mu,
                'sigma': params.sigma,
                'lambda': params.lambda_param,
                'a': params.a,
                'b': params.b
            }
        }
        
        if plot:
            self._plot_results(paths, T, current_price, results['expected_price'])
        
        return results

    def _plot_results(self, paths: np.ndarray, T: float, current_price: float, expected_price: float) -> None:
        """Create visualization plots"""
        plt.figure(figsize=(12, 6))
        
        plt.subplot(1, 2, 1)
        t = np.linspace(0, T, paths.shape[1])
        plt.plot(t, paths[np.random.choice(len(paths), 100)].T, alpha=0.1, color='blue')
        plt.plot(t, np.mean(paths, axis=0), 'r--', linewidth=2, label='Expected Path')
        plt.axhline(y=current_price, color='g', linestyle='--', label='Current Price')
        plt.title(f'{self.ticker_symbol} Simulated Price Paths')
        plt.xlabel('Time (years)')
        plt.ylabel('Price')
        plt.legend()
        
        # terminal distrib
        plt.subplot(1, 2, 2)
        sns.histplot(paths[:, -1], bins=50, stat='density')
        plt.axvline(x=current_price, color='g', linestyle='--', label='Current Price')
        plt.axvline(x=expected_price, color='r', linestyle='--', label='Expected Price')
        plt.title('Terminal Price Distribution')
        plt.xlabel('Price')
        plt.ylabel('Density')
        plt.legend()
        
        plt.tight_layout()
        plt.show()

analyzer = OptionsAnalyzer('HPE')

try:
    price_results = analyzer.analyze_price_expectations()
    strike_results = analyzer.analyze_strike_distribution()

    print("\nPrice Expectation Analysis:")
    print("-" * 40)
    for key, value in price_results.items():
        if isinstance(value, dict):
            print(f"\n{key}:")
            for k, v in value.items():
                print(f"  {k}: {v:.4f}")
        elif isinstance(value, float):
            print(f"{key}: {value:.4f}")
        else:
            print(f"{key}: {value}")
    
    print("\nStrike Price Analysis:")
    print("-" * 40)
    print(f"Current Price: ${strike_results['current_price']:.2f}")
    print(f"Volume-Weighted Strike: ${strike_results['volume_weighted_strike']:.2f}")
    print(f"Implied Return to VW Strike: {strike_results['implied_return_to_vw_strike']:.1%}")
    
    print("\nHigh Volume Strikes:")
    for strike_info in strike_results['high_volume_strikes']:
        print(f"Strike: ${strike_info['strike']:.2f}, "
                f"Volume: {strike_info['volume']}, "
                f"Implied Prob: {strike_info['implied_prob']:.1%}")
    
    print("\nReturn Components:")
    for component, value in strike_results['return_components'].items():
        print(f"{component}: {value:.1%}")
except Exception as e:
    print(f"Error in analysis: {str(e)}")
