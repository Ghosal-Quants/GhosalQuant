import asyncio
import sys

from ..core.types import Signal, SignalType, Order
from ..data_layer.datasource_base import DataSource
from ..execution.strategy_base import Strategy
from ..execution.sinks import SignalSink, PrintSink
from ..risk.risk_manager import RiskManager
from ..risk.types import RiskContext
from ..portfolio.portfolio_base import Portfolio
from ..brokerage.broker_base import Broker

class ExecutionSystem:
    def __init__(
            self,
            data_src: DataSource,
            strategy: Strategy,
            signal_destination: SignalSink | None = None,
            risk_manager: RiskManager | None = None,
            portfolio: Portfolio | None = None,
            broker: Broker | None = None
    ) -> None:

        self.data_src = data_src
        self.strategy = strategy

        # Signal pipeline
        self.sig_sink = signal_destination

        self.risk_manager = risk_manager
        self.portfolio = portfolio
        self.broker = broker

        if self.portfolio is not None and self.risk_manager is None:
            raise ValueError("A RiskManager has to be used in combination with a Portfolio.")


    async def run(self) -> None:
        if self.broker:
            await self.broker.connect()

        self.strategy.on_start()

        try:
            async for bar in self.data_src:
                self.portfolio.update_equity(bar)

                # Event handling
                signal: Signal | None = self.strategy.on_market_data(bar)

                # Strategy emitted no signal
                if signal is None:
                    continue

                # Actionable signal: send to sink
                if self.sig_sink is not None:
                    await self.sig_sink.on_signal(signal)

                if self.risk_manager:
                    order: Order | None = self.risk_manager.evaluate(signal, RiskContext())

                    if order is None:
                        continue

                    if self.portfolio is None:
                        continue

                    await self.portfolio.process_order(order)
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.strategy.on_end()

            if self.broker:
                await self.broker.disconnect()

            print("ExecutionSystem: Execution interrupted by user.", file=sys.stderr)
        except Exception as e:
            self.strategy.on_end()

            if self.broker:
                await self.broker.disconnect()

            raise e
        finally:
            self.strategy.on_end()

            if self.broker:
                await self.broker.disconnect()
