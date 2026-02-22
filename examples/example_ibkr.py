import asyncio

# Execution
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.data_layer.yfinance_datasource import YFinanceDataSource
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio

# Broker
from ghosalquant.brokerage.ibkr import IBKRBroker

# Visualisation helper
from ghosalquant.util.visualisation import plot_equity_curve

# Example strategy
from example_strategy import ExampleStrategy


if __name__ == "__main__":
    strategy = ExampleStrategy()

    data = YFinanceDataSource(
        symbol="^GSPC",
        start_day=1,
        start_month=1,
        start_year=2025,
        end_day=31,
        end_month=12,
        end_year=2025
    )

    portfolio = BasicPortfolio()

    execution_system = ExecutionSystem(
        strategy=strategy,
        data_src=data,
        risk_manager=RiskManager(),
        portfolio=portfolio,
        broker=IBKRBroker(port=4002)
    )

    asyncio.run(execution_system.run())

    print("Final Portfolio State: ")
    print(portfolio)

    plot_equity_curve(portfolio.equity_curve, show_realised_unrealised=True)
