# System Overview

## Tom's System Notes

### Rough components of system
- Signal generation system
  - Watch market & flag when to trade
- Risk management system
  - Monitor open & proposed positions
  - value at risk? xccy hedge?
- Order management system
  - take an order and split it into multiple orders to be executed at different brokers (or one! although they may not always have volume)
  - Broker (that can simulate matching orders)
- Market data system
  - Collate & manage market data
  - Maybe generate fake data for backtesting as well?
- Testing system
  - Backtesting
  - Forward testing
  - Stats (sharpe, drawdown etc etc)
- Interface
  - Dashboard
  - Strategy interaction
  - Testing interaction
  - Risk interaction


### Data Sources
- yfinance
- Polygon

### Version 1 for Backtesting (MVP)
- CSV based dumb iterative testing over static data
- Fetch
  - Fetch data from a library
  - Download as CSV
- Test
  - Strategy as a separate .py file with some logic (let's say simple moving average, buy at -2% & sell at +2%
  - Pass each bar to a simple moving average that calculates last value
  - Pass each bar to trading logic, which decides whether to buy or sell
  - Issue an order (market order to start with, move onto limit later)
- Execute
  - Add a broker to match orders
    - Probably pass it with a call instead of matching later on




## Possible MVP Backtesting & Execution System

## Component Diagram
<img src="diagrams/minimal_trading_system.png" alt="Minimal Trading System" width="400">

