from typing import Protocol
from core.types import Signal

class SignalSink(Protocol):
    async def on_signal(self, signal: Signal) -> None: ...

class PrintSink(SignalSink):
    async def on_signal(self, signal: Signal) -> None:
        print(f"Received signal: {signal}")

class BacktesterSink(SignalSink):
    def __init__(self, backtester) -> None:
        self.backtester = backtester

    async def on_signal(self, signal: Signal) -> None:
        await self.backtester.handle_signal(signal)

class BrokerSink(SignalSink):
    async def on_signal(self, signal: Signal) -> None:
        raise NotImplementedError("BrokerSink is not implemented yet.")

class RiskManagerSink(SignalSink):
    async def on_signal(self, signal: Signal) -> None:
        raise NotImplementedError("RiskManager is not implemented yet.")
