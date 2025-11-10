from src.data_layer.data import DataSource
from strategy_base import Strategy

class ExecutionSystem:
    def __init__(
            self,
            data_source: DataSource,
            strategy: Strategy,
            signal_destination, # TODO: Define type

    ) -> None:
        self.data_source = data_source
        self.strategy = strategy
        self.signal_destination = signal_destination # Backtester / Broker

    async def run(self) -> None:
        self.strategy.on_start()

        async for bar in self.data_source:
            ...

        self.strategy.on_end()
