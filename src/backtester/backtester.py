from execution.strategy_base import Strategy
from execution.execution_system import ExecutionSystem
from data_layer.data import DataSource
from execution.sinks import BacktesterSink
import asyncio

class Backtester:
    def __init__(self, strategy: Strategy, data_src: DataSource):
        self.strategy = strategy
        self.data_src = data_src
        self.execution_system = ExecutionSystem(
            data_src = self.data_src,
            strategy = self.strategy,
            signal_destination = BacktesterSink(self)
        )

    async def handle_signal(self, signal) -> None: ...

    def run(self): ...







