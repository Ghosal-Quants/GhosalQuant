from ib_insync import IB, Stock, MarketOrder, LimitOrder, StopOrder, StopLimitOrder, Trade as IBTrade

from .broker_base import Broker
from ..core.types import Fill, Order
from ..util.config import BrokerMode


class IBKRBroker(Broker):
    def __init__(
            self,
            host: str = "127.0.0.1",
            port: int = 7497,
            client_id: int = 1,
            mode: BrokerMode = BrokerMode.PAPER
    ) -> None:
        super().__init__()
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.mode = mode

        self._fill_queue: list[Fill] = []

    # ----- Managing Connection ----- #
    async def connect(self) -> None:
        await self.ib.connectAsync(
            host=self.host,
            port=self.port,
            clientId=self.client_id
        )

    async def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    # ----- Execution ----- #
    def submit(self, order: Order) -> str | None:
        raise NotImplementedError("IBKRBroker is not yet implemented.")

    def get_fills(self) -> list[Fill]:
        raise NotImplementedError("IBKRBroker is not yet implemented.")
