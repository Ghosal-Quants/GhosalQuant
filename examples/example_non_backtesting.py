from ghosalquant.data_layer import LivePolygonDataSource, YFinanceDataSource
from ghosalquant.execution import ExecutionSystem
from ghosalquant.portfolio import BasicPortfolio
from ghosalquant.risk import RiskManager
from ghosalquant.util.visualisation import plot_equity_curve

from example_strategy import ExampleStrategy

import os
from dotenv import load_dotenv
load_dotenv()
import asyncio


if __name__ == "__main__":
    # strategy receives market data and produces buy/sell signal
    strategy = ExampleStrategy()

    """
    # this is a sample implementation of what the data source could look like
    # DataSource is just a yfinance wrapper with hard coded variables 
    # it provides historical market data as an async iterable of Bar objects
    data = YFinanceDataSource(
        symbol="^GSPC",
        start_day=1,
        start_month=1,
        start_year=2025,
        end_day=31,
        end_month=12,
        end_year=2025
    )
    """
    data = LivePolygonDataSource(
        api_key = os.environ.get("POLYGON_API_KEY"),
        symbols = ["AAPL"],
        base_resolution = "1SEC",
        websocket_url = "wss://delayed.massive.com/stocks"
    )

    portfolio = BasicPortfolio()

    execution_system = ExecutionSystem(
        strategy=strategy,
        data_src=data,
        risk_manager=RiskManager(),
        portfolio=portfolio,
    )

    asyncio.run(execution_system.run())

    print("Final Portfolio State: ")
    print(portfolio)

    plot_equity_curve(portfolio.equity_curve, show_realised_unrealised=True)
