from .broker_base import Broker
from ..core.types import Fill, Order

class IBKRBroker(Broker):
    # ----- Managing Connection ----- #
    def connect(self) -> None:
        raise NotImplementedError("IBKRBroker is not yet implemented.")

    def disconnect(self) -> None:
        raise NotImplementedError("IBKRBroker is not yet implemented.")

    # ----- Execution ----- #
    def submit(self, order: Order) -> str | None:
        raise NotImplementedError("IBKRBroker is not yet implemented.")

    def get_fills(self) -> list[Fill]:
        raise NotImplementedError("IBKRBroker is not yet implemented.")
