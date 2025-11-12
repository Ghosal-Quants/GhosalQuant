from execution.execution_system import ExecutionSystem
from example_strategy import ExampleStrategy
from data_layer.data import DataSource
from execution.sinks import PrintSink
import asyncio

if __name__ == "__main__":
    print("Example Implementation With Just Print: ")
    
    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()
    
    # this is a sample implementation of what the data source could look like
    # DataSource is just a yfinance wrapper with hard coded variables 
    # it provides historical market data as an async iterable of Bar objects
    data = DataSource()

    # Note: signal_destination is already PrintSink by default
    execution_system = ExecutionSystem(strategy=strategy, data_src=data, signal_destination=PrintSink())

    asyncio.run(execution_system.run())
