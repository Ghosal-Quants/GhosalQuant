import asyncio

from ghosalquant.util.visualisation import plot_equity_curve

# Execution
from ghosalquant.execution import ExecutionSystem
from ghosalquant.data_layer import LiveIBKRDataSource
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from example_strategy import ExampleStrategy

# Broker
from ghosalquant.brokerage import IBKRBroker


if __name__ == "__main__":
    strategy = ExampleStrategy()

    data = LiveIBKRDataSource(
        symbols=["AAPL"],
        # port=4002, # 7497 default for TWS, 4002 default for IB Gateway
        client_id=2
    )

    portfolio = BasicPortfolio()

    execution_system = ExecutionSystem(
        strategy=strategy,
        data_src=data,
        risk_manager=RiskManager(),
        portfolio=portfolio,
        # broker=IBKRBroker(port=4002)
        broker=IBKRBroker(port=7497)
    )

    asyncio.run(execution_system.run())

    print("Final Portfolio State: ")
    print(portfolio)

    plot_equity_curve(portfolio.equity_curve, show_realised_unrealised=True)
