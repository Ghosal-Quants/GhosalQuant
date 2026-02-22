from .broker_base import Broker
from ..core.types import Fill, Order

class SimBroker(Broker):
    def submit(self, order: Order) -> str | None:
        raise NotImplementedError("SimBroker does not implement order submission yet")

    def get_fills(self) -> list[Fill]:
        raise NotImplementedError("SimBroker does not implement fills yet")
