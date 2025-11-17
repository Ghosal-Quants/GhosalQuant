from core.types import Signal, SignalType
from data_layer.data import DataSource
from execution.strategy_base import Strategy
from execution.sinks import SignalSink, PrintSink

# ExecutionSystem coordinates the backtesting loop:
#   - receives data
#   - feeds it into the strategy
#   - TODO: redirect the signals to a RiskManger or Broker

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

                # Strategy emitted no signal (interpreted same as SignalType.NOOP)
                if signal is None:
                    continue

                # Strategy emitted an explicit NOOP signal
                # (meaning "hold" / "no action")
                if signal.signal_type is SignalType.NOOP:
                    continue

                # Actionable signal: send to sink
                await self.sig_sink.on_signal(signal)
        finally:
            self.strategy.on_end()
