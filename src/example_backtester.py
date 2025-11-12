from backtesting.backtester import Backtester
import asyncio

class ExampleBacktester(Backtester):
    async def handle_signal(self, signal):
        # Dummy implementation
        print(f"[BACKTESTER]: {signal}")

    def run(self):
        # ExecutionSystem coordinates the backtesting loop:
        #   - fetches historical data
        #   - feeds it into the strategy
        #   - TODO: redirect the signals to a RiskManger or Broker

        # ExecutionSystem.run() is an async routine so it must be run
        # with asyncio.run() to start the event loop
        asyncio.run(self.execution_system.run())
