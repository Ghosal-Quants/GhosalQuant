"""
Modified Monte Carlo simulation script. 

As with a lot of things in stochastic simualtion, the best way to carry out this simulation is 
not universally agreed upon (as far as I can tell). I will try and signpost where a decision has 
been made which realistically could have gone another way. 

In general, the approach will be to find the distribution of the log of the daily ratios in prices. That is, 
we find the distribution of log(P_{t+1} / P_t). The log is taken as this generally will improve numerical 
stability, and apparently this is common in financial time series analysis. 

We will model future daily changes as being drawn from a normal distribution, and our price time series will be  
P_{t+1} = P_{t} * e ^{r_t}, where r_t ~ N(mu, sigma^2) (i.e., r will be normally distributed with mean mu 
and standard deviation sigma squared, both of which will be extracted from the log of the daily ratios in prices) 
"""

import pandas as pd
import numpy as np 
import tqdm
from datetime import timedelta
import yfinance as yf
import matplotlib.pyplot as plt
from scipy.stats import norm
from typing import Optional

class MonteCarloSim:
    """
    Monte Carlo simulation for stock prices using a log-normal returns model.

    Parameters:
        ticker (str): Stock ticker for simulation.
        start (str): Start date for historical data retrieval (default: 1 year ago).
        end (str): End date for historical data retrieval (default: today).
        end_sim (str): End date for forecast period (default: 90 days from today).
        num_sims (int): Number of simulation paths to generate.
        num_plotted_sims (int): Number of simulations to plot.
        show_fitted_dist (bool): Whether to plot fitted distribution.
        save_plot (bool): Whether to save the plot to a file.
        alpha_sig (str): Significance level of confidence bound on the simulation.
    """
    
    def __init__(self, 
                 ticker: str, 
                 start: Optional[str] = None, # default start 1 year ago
                 end: Optional[str] = None,   # default end on present day
                 end_sim: Optional[str] = None, # default 90 day forecast
                 num_sims: int = 1000,
                 num_plotted_sims: int = 100,
                 show_fitted_dist: bool = False,
                 save_plot: bool = False,
                 alpha_sig: str = 0.05): #significance value alpha
        
        today = pd.to_datetime('now').date()
        self.start = start if start else str(today + timedelta(days=-365))
        self.end = end if end else str(today)
        self.end_sim = end_sim if end_sim else str(today + timedelta(days=90))
        

        if num_sims <= 0:
            raise ValueError("Number of simulations (num_sims) must be a positive integer.")
        if num_plotted_sims <= 0:
            raise ValueError("Number of simulations to plot (num_plotted_sims) must be a positive integer.")
        if alpha_sig < 0 or alpha_sig > 1:
            raise ValueError("Significance level must be a value between 0 and 1.")
        
        # Assign attributes
        self.ticker = ticker
        self.sim_count = num_sims
        self.sims_plotted = num_plotted_sims
        self.plotter = show_fitted_dist
        self.save = save_plot
        self.alpha_sig = alpha_sig


    def get_price_data(self):
        """
        Fetches historical price data for the given tickers from Yahoo Finance.
        
        Returns:
            pd.DataFrame: DataFrame containing adjusted close prices for each ticker, populated with
            log ratios of daily changes. 
        """
        # Fetch data for each ticker
        try:
            data = yf.download(self.ticker, start=self.start, end=self.end, interval="1d")["Adj Close"]
        except Exception as e:
            raise ValueError(f"Failed to fetch data for ticker {self.ticker}: {e}")
        
        data.ffill(inplace=True)
        data['Log_ratios'] = np.log(data[self.ticker] / data[self.ticker].shift(1))
        data.dropna(inplace = True)
        return data
    
    def fit_dist(self, data):
        """
        Fits a normal distribution to the stock price changes, excluding the bottom and top 2 percentiles 
        to reduce overestimation of the standard deviation. This cutoff is chosen arbitrarily.

        Parameters:
            data (pd.DataFrame): DataFrame containing log ratios of daily price changes.

        Returns:
            dict: Dictionary containing the initial price, start date, mean, and standard deviation of the 
                fitted normal distribution.
        """
        lower_quantile, upper_quantile = data['Log_ratios'].quantile(0.02), data['Log_ratios'].quantile(0.98)
        filtered_data = data[(data['Log_ratios'] >= lower_quantile) & (data['Log_ratios'] <= upper_quantile)]

        mean = data['Log_ratios'].mean()
        standard_dev = filtered_data['Log_ratios'].std()

        # Output parameter estimates
        print(f"Estimated Parameters for {self.ticker}:")
        print(f"Drift (mean): {mean:.6f}")
        print(f"Volatility (std dev): {standard_dev:.6f}")

        if self.plotter == True:
            x = np.linspace(-0.1, 0.1, 100)
            y = norm.pdf(x, mean, standard_dev)
            plt.figure(figsize=(14, 5.5))
            plt.hist(data['Log_ratios'], bins = 100, range = (-0.1,0.1), density = True, label = 'Data')
            plt.plot(x, y, label=f"Normal Dist. (mean={mean:3f}, standard_dev ={standard_dev:3f})")
            font_settings = {'fontname': 'Arial', 'fontsize': 16}
            plt.title('Distribution of price changes vs model', **font_settings)
            
            plt.xlabel('Log ratio of prices', **font_settings)
            plt.ylabel("Simulated Price", **font_settings)
            plt.xticks(**font_settings)
            plt.yticks(**font_settings)

            plt.grid()
            plt.legend()
            if self.save == True:
                plt.savefig(f'Model distribution for {self.ticker}.png', dpi = 500, bbox_inches = 'tight')
            plt.show()

        params = {'Start':data[f'{self.ticker}'].iloc[-1],
                'Start_date':data.index[-1],
                'mean' : mean, 
                'standard_dev': standard_dev}
        return params
    
    def create_trajectory(self, params):
        """
        Generates a simulated price trajectory from the start date until the end of the simulation timeline.

        Parameters:
            params (dict): Dictionary containing 'Start' (initial price), 'Start_date' (starting date), 
                        'mean', and 'standard_dev' for log-normal returns.

        Returns:
            pd.DataFrame: DataFrame indexed by date, containing the forecasted price trajectory.
        """
        start = str(params['Start_date'].date())
        date_range = pd.date_range(start=start, end=self.end_sim, freq='D')
        price_list = [params['Start']]

        for date in date_range:
            if str(date.date()) == start:
                continue
            if date.weekday() >= 5:  # 5 is Saturday, 6 is Sunday
                # Duplicate the previous price for weekends
                price_list.append(price_list[-1])  
            else:
                # Calculate or fetch the next price here, for example:
                log_ratio_change = norm.rvs(params['mean'], params['standard_dev'])
                exponentiated_log_ratio = np.exp(log_ratio_change)
                price_list.append(price_list[-1] * exponentiated_log_ratio)#

        return (pd.DataFrame(price_list, index = date_range, columns = ['Forecast']))
    
    def collect_trajectories(self, data, params):
        """
        Collects a specified number of simulated trajectories.

        Parameters:
            data (pd.DataFrame): Historical data used for reference in creating trajectories.
            params (dict): Dictionary with initial price, start date, mean, and standard deviation for simulation.

        Returns:
            pd.DataFrame: DataFrame where each column represents a simulated price trajectory.
        """
        np.random.seed(1)

        trajectories = [
            self.create_trajectory(params).rename(columns={'Forecast': f'Trajectory_{i}'})
            for i in tqdm.tqdm(range(self.sim_count), desc="Simulating Trajectories")
        ]

        trajectories = pd.concat(trajectories, axis=1)
        return trajectories

    def plots_and_percentiles(self, sim_color='blue', avg_color='black', sim_alpha=0.05):
        """
        Runs Monte Carlo simulations, plots selected simulations with percentiles, and calculates quantiles 
        of minimum and end-point values from the trajectories.

        Parameters:
            sim_color (str): Color for the individual simulation lines. Defaults to 'blue'.
            avg_color (str): Color for the average simulation line. Defaults to 'black'.
            sim_alpha (float): Transparency level for simulation lines. Defaults to 0.05.

        Returns:
            pd.DataFrame: DataFrames containing quantiles for minimum values and end-point values.
        """
        # Fetch data, fit distribution, and generate trajectories
        data = self.get_price_data()
        params = self.fit_dist(data)
        trajectories = self.collect_trajectories(data, params)

        # Calculate the 100(1-alpha)% confidence interval for each time step
        lower_bound = trajectories.quantile(self.alpha_sig/2, axis=1)
        upper_bound = trajectories.quantile(1 - self.alpha_sig/2, axis=1)

        # Plot selected simulations and average
        selected_sims = np.random.choice(trajectories.columns, size=self.sims_plotted, replace=False)
        
        plt.figure(figsize=(14, 7))
        plt.plot(trajectories[selected_sims], alpha=sim_alpha, color=sim_color)
        plt.plot(trajectories.mean(axis=1), color=avg_color, label='Average Simulation')
        plt.plot(data[self.ticker][-40:], color=avg_color)

        # Plot the 95% confidence interval
        plt.plot(lower_bound.index, lower_bound, color='red', linewidth=1, label=f'{100*(1-self.alpha_sig)}% Confidence Bounds')
        plt.plot(upper_bound.index, upper_bound, color='red', linewidth=1)

        font_settings = {'fontname': 'Arial', 'fontsize': 16}
        plt.xlabel("Date", **font_settings)
        plt.ylabel("Simulated Price", **font_settings)
        plt.title(f"Monte Carlo Simulation of {self.ticker} Stock Price", **font_settings)
        plt.xticks(**font_settings)
        plt.yticks(**font_settings)
        plt.legend()
        plt.grid()
        
        if self.save:
            plt.savefig(f'MC_sim_for_{self.ticker}.png', dpi=500, bbox_inches='tight')
        plt.show()



        # Helper function for quantile extraction
        def calculate_quantiles(series, label):
            df =  pd.DataFrame({
                'Purpose': [label],
                'confidence_upper': [np.percentile(series, (1-self.alpha_sig/2)*100)],
                'seventy_fifth': [np.percentile(series, 75)],
                'fiftieth': [np.percentile(series, 50)],
                'twenty_fifth': [np.percentile(series, 25)],
                'confidence_lower': [np.percentile(series, (self.alpha_sig/2)*100)]
            })
            return df

        # Calculate quantiles for minimum and end-point values
        min_values = trajectories.min(axis=0).sort_values()
        end_points = trajectories.iloc[-1]
        quantiles1 = calculate_quantiles(min_values, "Quantiles of Minimum Values")
        quantiles2 = calculate_quantiles(end_points, "Quantiles of End-Point Values")

        # Current, expected, and quantile values
        current_price = data[self.ticker].iloc[-1]
        expected_price = end_points.mean()
        quantile_25 = np.percentile(end_points, 100*(self.alpha_sig/2))
        quantile_975 = np.percentile(end_points, 100*(1-self.alpha_sig/2))

        # Plot histogram of end-point values
        plt.figure(figsize=(14, 7))
        plt.hist(end_points, bins=50, color='gray', alpha=0.7, label='End-Point Prices')
        plt.axvline(current_price, color='blue', linestyle='--', linewidth=2, label='Current Price')
        plt.axvline(expected_price, color='green', linestyle='--', linewidth=2, label='Expected Price (Mean)')
        plt.axvline(quantile_25, color='red', linestyle='--', linewidth=2) #Confidence lower bound
        plt.axvline(quantile_975, color='red', linestyle='--', linewidth=2, label=f'{100*(1-self.alpha_sig)}% Confidence Bounds') #Upper bound
        plt.title(f'Histogram of Simulated End-Point Prices for {self.ticker}', **font_settings)
        plt.xlabel('End-Point Prices', **font_settings)
        plt.ylabel('Frequency', **font_settings)
        plt.xticks(**font_settings)
        plt.yticks(**font_settings)
        plt.legend()
        plt.grid()
        plt.show()

        print(f'{100*(1-self.alpha_sig/2)} confidence')
        print(f'{100*(self.alpha_sig/2)} confidence lb')


        return quantiles1, quantiles2
