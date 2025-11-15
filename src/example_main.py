from datetime import datetime
from example_strategy import ExampleStrategy
from data_layer.example_datasource import YFinanceDataSource
from backtester.example_backtester import ExampleBacktester

if __name__ == "__main__":
    print("Example Implementation With Backtester: ")
    
    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()
    
    # this is a sample implementation of what the data source could look like
    # DataSource is just a yfinance wrapper with hard coded variables 
    # it provides historical market data as an async iterable of Bar objects
    data = YFinanceDataSource(
        symbol="^GSPC",
        start=datetime(2020, 1, 1),
        end=datetime(2020, 1, 28)
    )

    backtester = ExampleBacktester(strategy=strategy, data_src=data)
    backtester.run()
