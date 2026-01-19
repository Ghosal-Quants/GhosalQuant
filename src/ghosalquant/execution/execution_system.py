from ..core.types import Signal, SignalType, Order
from ..data_layer.datasource_base import DataSource
from ..execution.strategy_base import Strategy
from ..execution.sinks import SignalSink, PrintSink
from ..risk.risk_manager import RiskManager
from ..risk.types import RiskContext


# ExecutionSystem coordinates the backtesting loop:
#   - receives data
#   - feeds it into the strategy
#   - TODO: redirect the signals to a RiskManger or Broker

class ExecutionSystem:
    def __init__(
            self,
            data_src: DataSource,
            strategy: Strategy,
            signal_destination: SignalSink | None = None,
            risk_manager: RiskManager | None = None,
    ) -> None:

        self.data_src = data_src
        self.strategy = strategy

        # Signal pipeline
        self.sig_sink = signal_destination or PrintSink() # Backtester / Broker - Print by default

        # Risk manager
        self.risk_manager = risk_manager

    async def run(self) -> None:
        self.strategy.on_start()

        try:
            async for bar in self.data_src:
                # Event handling
                signal: Signal | None = self.strategy.on_market_data(bar)

                # Strategy emitted no signal
                if signal is None:
                    continue

                # Actionable signal: send to sink
                await self.sig_sink.on_signal(signal)

                if self.risk_manager:
                    order: Order = self.risk_manager.evaluate(signal, RiskContext())
                    print("Order:", str(order))
                    raise NotImplementedError("Risk manager integration is not fully implemented yet.")
        finally:
            self.strategy.on_end()
