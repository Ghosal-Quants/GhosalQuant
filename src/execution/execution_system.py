from core.types import Signal
from data_layer.data import DataSource
from execution.strategy_base import Strategy
from execution.sinks import SignalSink, PrintSink

class ExecutionSystem:
    def __init__(
            self,
            data_src: DataSource,
            strategy: Strategy,
            signal_destination: SignalSink | None = None
    ) -> None:

        self.data_src = data_src
        self.strategy = strategy
        self.sig_sink = signal_destination or PrintSink() # Backtester / Broker - Print by default

    async def run(self) -> None:
        self.strategy.on_start()

        try:
            async for bar in self.data_src:
                # Event handling
                signal: Signal | None = self.strategy.on_market_data(bar)
                if signal is not None:
                    await self.sig_sink.on_signal(signal)
        finally:
            self.strategy.on_end()
