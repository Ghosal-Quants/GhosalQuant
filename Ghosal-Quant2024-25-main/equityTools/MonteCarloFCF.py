import numpy as np
import matplotlib.pyplot as plt
import yfinance as yf
from datetime import datetime, timedelta

# Historical FCF data
years = [2017, 2018, 2019, 2020, 2021, 2022, 2023]
fcf_values = [521, 764, 581, 950, 705, 544, 1183]

# Base case projections for FCF from 2024 to 2032
base_case_years = list(range(2024, 2033))
base_case_fcf = [1280, 1349, 1410, 1462, 1503, 1531, 1545, 1545, 1531]

# Monte Carlo Simulation parameters
num_simulations = 1000000
num_years_forecast = len(base_case_fcf)

# Calculate mean and standard deviation from historical data
mean_fcf = np.mean(fcf_values)
std_fcf = np.std(fcf_values)

# Initialize the standard deviation with the historical data
std_fcf_dynamic = std_fcf

# Run Monte Carlo Simulation
forecasted_fcf = []
for _ in range(num_simulations):
    forecast = []
    for i in range(num_years_forecast):
        if i == 0:
            # First year's projection is based on the base case and historical mean
            next_fcf = np.random.normal(base_case_fcf[i], std_fcf)
        else:
            # Subsequent years are based on the previous forecast with randomness
            std_fcf_dynamic = np.std(forecast) if len(forecast) > 1 else std_fcf
            next_fcf = np.random.normal(forecast[-1] * (1 + 0.05), std_fcf)
        forecast.append(next_fcf)  
    forecasted_fcf.append(forecast)

# Calculate average forecasted FCF for each year
average_forecast = np.mean(forecasted_fcf, axis=0)

# Plot the historical and forecasted FCF
plt.figure(figsize=(12, 7))
cmap = plt.get_cmap('hsv')

# Plot historical FCF
plt.plot(years, fcf_values, color='black', linewidth=2, marker='o', label='Historical FCF')

# Connect historical FCF to base case projection
plt.plot([2023, 2024], [fcf_values[-1], base_case_fcf[0]], color='blue', linewidth=2, linestyle='--')

# Plot base case projection
plt.plot(base_case_years, base_case_fcf, color='blue', linewidth=2, linestyle='--', marker='o', label='Base Case Projection')

# Plot a random sample of 100 forecasted FCF and connect 2023 to 2024
random_indices = np.random.choice(num_simulations, 100, replace=False)
for count, i in enumerate(random_indices):
    plt.plot([2023, 2024], [fcf_values[-1], forecasted_fcf[i][0]], color=cmap(count / 100), alpha=0.3)
    plt.plot(base_case_years, forecasted_fcf[i], color=cmap(count / 100), alpha=0.3)

# Plot average forecast
plt.plot(base_case_years, average_forecast, color='red', linewidth=2, label='Average Forecast')

plt.xlabel('Year')
plt.ylabel('FCF')
plt.title('Monte Carlo Simulation of FCF Forecast (Next 10 Years) Based on Base Case')
plt.legend()
plt.grid()
plt.show()

# Discounted Cash Flow parameters
discount_rate = 0.0691  # WACC
terminal_growth_rate = 0.02  # Assume 2% terminal growth rate
value_of_cash = 1279  # Cash on balance sheet
value_of_debt = 0  # Debt on balance sheet
shares_outstanding = 191  # Shares outstanding
ticker = "GRMN"  # Ticker of we are looking at

# IN CASE ONE MAY WANT TO NOT HRDCODE THE PRICE YOU CAN USE IT FROM THERE:
# stock_data = yf.download(ticker, start=(datetime.today() - timedelta(days=2)), end=(datetime.today()), interval="1d")  # Retrieve data for the ticker

current_share_price = 123  # Default value for this example

# Calculate the present value of forecasted FCF for each simulation
total_pv_simulations = []
for forecast in forecasted_fcf:
    pv_forecast = 0
    for year in range(num_years_forecast):
        discount_factor = (1 + discount_rate) ** (year + 1)
        pv_forecast += forecast[year] / discount_factor
    terminal_value = forecast[-1] * (1 + terminal_growth_rate) / (discount_rate - terminal_growth_rate)
    pv_terminal_value = terminal_value / ((1 + discount_rate) ** num_years_forecast)
    total_pv_simulations.append(pv_forecast + pv_terminal_value)

# Calculate the average DCF valuation
average_dcf_valuation = np.mean(total_pv_simulations)
equity_value = average_dcf_valuation + value_of_cash - value_of_debt
implied_share_price = equity_value / shares_outstanding

print(f"Average DCF Valuation: ${average_dcf_valuation:,.2f}")
print(f"ISP: ${implied_share_price:,.2f}")

# Calculate implied share price percentiles
percentiles = [95, 75, 50, 25, 5]
percentile_values = np.percentile(total_pv_simulations, percentiles)
percentile_prices = (percentile_values + value_of_cash - value_of_debt) / shares_outstanding

# Create a table of percentiles and implied prices
print("\nPercentile\t\tImplied Price\t\tDiscount to Current Price (%)")
if current_share_price is not None:
    for i, p in enumerate(percentiles):
        discount = ((percentile_prices[i] - current_share_price) / current_share_price) * 100
        print(f"{p}th Percentile\t\t${percentile_prices[i]:,.2f}\t\t{discount:.2f}%")
else:
    for i, p in enumerate(percentiles):
        print(f"{p}th Percentile\t\t${percentile_prices[i]:,.2f}")
