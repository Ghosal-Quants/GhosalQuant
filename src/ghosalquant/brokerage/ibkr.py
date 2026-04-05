from datetime import datetime, timezone
from typing import Iterable

from ib_insync import (
    IB,
    Stock,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    Trade as IBTrade,
    Fill as IBFill,
    Contract
)

from .broker_base import Broker
from ..core.types import Fill, Order, SignalType
from ..util.config import BrokerMode


class IBKRBroker(Broker):
    def __init__(
            self,
            host: str = "127.0.0.1",
            port: int = 7497,
            client_id: int = 1,
            mode: BrokerMode = BrokerMode.PAPER,
            connect_timeout_s: float = 5.0,
            handshake_timeout_s: float = 5.0,
            # Default to sending stock order to SMART
            contract_overrides: Iterable[Contract] | None = None
    ) -> None:
        super().__init__()
        self.ib = IB()
        self.host = host
        self.port = port
        self.client_id = client_id
        self.mode = mode
        self.connect_timeout_s = float(connect_timeout_s)
        self.handshake_timeout_s = float(handshake_timeout_s)

        # lookup for contract overrides
        self.contract_map = {c.symbol: c for c in (contract_overrides or [])}

        self._fill_queue: list[Fill] = []
        self._events_wired = False

    # ----- Managing Connection ----- #
    async def connect(self) -> None:
        if self.ib.isConnected():
            return

        await self.ib.connectAsync(
            host=self.host,
            port=self.port,
            clientId=self.client_id
        )

        if not self._events_wired:
            self.ib.execDetailsEvent += self._on_exec_details
            self._events_wired = True

    async def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    # ----- Execution ----- #
    def submit(self, order: Order) -> str | None:
        if not self.ib.isConnected():
            raise RuntimeError("IBKRBroker is not connected. Call connect() before submitting orders.")

        contract = self.contract_map.get(
            order.symbol,
            Stock(order.symbol, "SMART", "USD")
        )
        action = "BUY" if order.side is SignalType.BUY else "SELL"
        ib_order = MarketOrder(action, float(order.quantity))

        trade: IBTrade = self.ib.placeOrder(contract, ib_order)
        order_id = getattr(trade.order, "orderId", None)
        return str(order_id) if order_id else None

    def get_fills(self) -> list[Fill]:
        fills = self._fill_queue
        self._fill_queue = []
        return fills

    # ----- Helpers ----- #
    def _on_exec_details(self, _trade: IBTrade, fill: IBFill) -> None:
        """
        Callback for execDetailsEvent, when a fill occurs.
        """
        execution = fill.execution
        contract = fill.contract

        side = SignalType.BUY if execution.side == "BOT" else SignalType.SELL
        symbol = (
                getattr(contract, "symbol", None)
                or getattr(contract, "localSymbol", None)
                or "UNKNOWN"
        )

        self._fill_queue.append(
            Fill(
                time_stamp=fill.time if isinstance(fill.time, datetime) else datetime.now(timezone.utc),
                symbol=symbol,
                side=side,
                quantity=float(getattr(execution, "shares", 0.0)),
                price=float(getattr(execution, "price", 0.0)),
                broker_order_id=str(getattr(execution, "orderId", "")) or None,
                commission=None,
            )
        )
