from ghosalquant.execution.execution_system import ExecutionSystem
from example_strategy import ExampleStrategy
from ghosalquant.data_layer.yfinance_datasource import YFinanceDataSource
from ghosalquant.execution.sinks import PrintSink
import asyncio

if __name__ == "__main__":
    print("Example Implementation With Just Print: ")
    
    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()
    
    # this is a sample implementation of what the data source could look like
    # DataSource is just a yfinance wrapper with hard coded variables 
    # it provides historical market data as an async iterable of Bar objects
    data = YFinanceDataSource(
        symbol="^GSPC",
        start_day=1,
        start_month=1,
        start_year=2020,
        end_day=28,
        end_month=1,
        end_year=2020
    )

    # Note: signal_destination is already PrintSink by default
    execution_system = ExecutionSystem(strategy=strategy, data_src=data, signal_destination=PrintSink())

    asyncio.run(execution_system.run())
