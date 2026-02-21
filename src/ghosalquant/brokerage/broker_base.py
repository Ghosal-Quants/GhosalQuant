from abc import ABC, abstractmethod

from ..core.types import Order, Fill

class Broker(ABC):
    # ----- Managing Connection ----- #
    def connect(self) -> None:
        """
        Optionally can be overridden by subclasses if connection management is needed on startup
        (no-op for simulation brokers)
        """
        return None

    def close(self) -> None:
        """
        Optionally can be overridden by subclasses if connection management is needed on shutdown
        (no-op for simulation brokers)
        """
        return None

    # ----- Execution ----- #
    @abstractmethod
    def submit(self, order: Order) -> str | None:
        """
        Submit an order using a broker-specific API

        Optionally returns a broker-specific order ID for tracking purposes
        """
        ...

    @abstractmethod
    def get_fills(self) -> list[Fill]:
        """
        Get fills since the last call

        Implementations should return a possibly empty list, and drain any internal fill queue
        """
        ...