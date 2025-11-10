# Ghosal Investment Fund Dashboard

A Streamlit-based web application for visualizing and analyzing the performance of Lancaster University's student-led investment fund portfolios.

## Overview

The dashboard provides real-time analysis of active and passive investment portfolios managed by the Ghosal Investment Fund, comparing performance against the MSCI ACWI benchmark.

## Project Structure

```
├── Home.py                 # Landing page and main dashboard
├── pages/
│   ├── Active Portfolio.py    # Active portfolio analysis
│   └── Passive Portfolio.py   # Passive portfolio analysis
├── utils.py                   # Utility functions
├── refactoredSheetdata.py    # Data processing
└── cacheFolder               # Data cache
```

## Dependencies

- streamlit
- pandas
- numpy
- yfinance
- scipy
- requests
- pytz
- datetime

## Core Components

### Data Pipeline

#### StockDataFetcher Class

- Retrieves and processes data from Google Sheets
- Handles ticker validation and data normalization

#### PortfolioTracker Class

- Manages exchange rates and historical price data
- Calculates portfolio values and performance metrics

### Frontend Pages

- **Home**: Fund overview, team info, and performance charts
- **Portfolio Pages**: Performance tracking, statistics, and composition analysis
- **Utilities**: Data processing, caching, and analytics

## Data Flow

1. Data fetching from Google Sheets API
2. Historical price data retrieval from Yahoo Finance
3. Currency conversion and calculations
4. Performance metric computation
5. Frontend visualization

## Usage

Run the application:

```bash
streamlit run Home.py
```

## Data Sources

- Portfolio data: Google Sheets API
- Market data: Yahoo Finance API
- Benchmark: MSCI ACWI index

## Performance Metrics

- Volatility
- Sharpe ratio
- Alpha/Beta
- Maximum drawdown
- R-squared

## System Features

- Daily data caching
- Multi-currency support
- Real-time performance tracking
- Error handling and validation
- API optimization

## Notes

- Values in GBP
- 252 trading days per year
- 5% risk-free rate for Sharpe calculations
- Daily exchange rate updates
- Cached historical data

## Contributing

Contact the Ghosal Investment Fund team at Lancaster University for contribution guidelines.
