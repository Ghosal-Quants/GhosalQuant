from execution.execution_system import ExecutionSystem
from example_strategy import ExampleStrategy
from data_layer.data import DataSource
import asyncio

if __name__ == "__main__":
    print("Example Implementation: ")
    
    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()
    
    # this is a sample implementation of what the data source could look like
    # DataSource is just a yfinance wrapper with hard coded variables 
    # it provides historical market data as an async iterable of Bar objects
    data = DataSource()

    # ExecutionSystem coordinates the backtesting loop:
    #   - fetches historical data
    #   - feeds it into the strategy
    #   - TODO: redirect the signals to a) RiskManger or b) Backtester
    execution = ExecutionSystem(data_source=data, strategy=strategy)

    # ExecutionSystem.run() is an async routine so it must be run
    # with asyncio.run() to start the event loop
    asyncio.run(execution.run())


