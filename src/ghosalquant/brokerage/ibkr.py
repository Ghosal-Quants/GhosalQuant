from datetime import datetime, timezone
from typing import Iterable
import logging

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
from ..core.types import Fill, OpenOrderView, Order, SignalType
from ..util.config import BrokerMode

logger = logging.getLogger(__name__)


class IBKRBroker(Broker):
    """
    Live and paper trading through Interactive Brokers.

    Resting orders are reported and cancellable (``OpenOrderQueries``), read from what IB says it
    is working rather than from this process's memory, so an order placed before a restart is
    still visible. The exposure and cash-allocation risk rules need that: they measure committed
    capital from ``RiskContext.open_orders``, which stays empty for a broker that cannot report.

    Executions are also deduplicated by ``execId`` here. ``ib_insync`` already does this inside
    its wrapper, so this is a second line of defence over behaviour that is not part of its
    documented contract, not a fix for anything observed.
    """

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

        self._seen_exec_ids: set[str] = set()
        self._orders_by_id: dict[str, Order] = {}
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
        limit = order.declared_limit_price
        if limit is None:
            ib_order = MarketOrder(action, float(order.quantity))
        else:
            ib_order = LimitOrder(action, float(order.quantity), float(limit))

        trade: IBTrade = self.ib.placeOrder(contract, ib_order)
        order_id = getattr(trade.order, "orderId", None)
        if not order_id:
            return None
        broker_order_id = str(order_id)
        # Kept so a resting order reports the metadata the strategy set, not a reconstruction.
        self._orders_by_id[broker_order_id] = order
        return broker_order_id

    def get_fills(self) -> list[Fill]:
        fills, self._fill_queue = self._fill_queue, []
        return fills

    # ----- OpenOrderQueries ----- #
    def open_order_views(self, symbol: str | None = None) -> tuple[OpenOrderView, ...]:
        """Resting orders as IB reports them, which includes any placed before this process started."""
        views = []
        for trade in self.ib.openTrades():
            contract_symbol = getattr(trade.contract, "symbol", None) or "UNKNOWN"
            if symbol is not None and contract_symbol != symbol:
                continue
            remaining = float(trade.remaining())
            if remaining <= 0.0:
                continue
            views.append(
                OpenOrderView(
                    broker_order_id=str(getattr(trade.order, "orderId", "") or ""),
                    order=self._order_for(trade, contract_symbol, remaining),
                    remaining_quantity=remaining,
                    submitted_at=_trade_time(trade),
                    limit_price=_limit_price(trade),
                )
            )
        return tuple(views)

    def cancel_open_orders(self, symbol: str, side: SignalType | None = None) -> int:
        cancelled = 0
        for trade in self.ib.openTrades():
            if (getattr(trade.contract, "symbol", None) or "UNKNOWN") != symbol:
                continue
            if side is not None and _side_of(getattr(trade.order, "action", "")) is not side:
                continue
            self.ib.cancelOrder(trade.order)
            cancelled += 1
        return cancelled

    # ----- Helpers ----- #
    def _on_exec_details(self, _trade: IBTrade, fill: IBFill) -> None:
        """
        Callback for execDetailsEvent, when a fill occurs.
        """
        execution = fill.execution
        contract = fill.contract

        exec_id = str(getattr(execution, "execId", "") or "")
        if exec_id and exec_id in self._seen_exec_ids:
            logger.debug("IBKRBroker ignoring execution %s: already seen", exec_id)
            return
        if exec_id:
            self._seen_exec_ids.add(exec_id)

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
                commission=_commission(fill),
            )
        )

    def _order_for(self, trade: IBTrade, symbol: str, remaining: float) -> Order:
        """The order we submitted, or one rebuilt from IB for anything this process did not place."""
        known = self._orders_by_id.get(str(getattr(trade.order, "orderId", "") or ""))
        if known is not None:
            return known
        limit = _limit_price(trade)
        return Order(
            time_stamp=_trade_time(trade),
            symbol=symbol,
            side=_side_of(getattr(trade.order, "action", "BUY")),
            quantity=float(getattr(trade.order, "totalQuantity", remaining) or remaining),
            ref_price=limit if limit else 0.0,
            limit_price=limit,
        )


def _commission(fill: IBFill) -> float | None:
    report = getattr(fill, "commissionReport", None)
    commission = getattr(report, "commission", None)
    return None if commission is None else float(commission)


def _limit_price(trade: IBTrade) -> float | None:
    limit = getattr(trade.order, "lmtPrice", None)
    return float(limit) if limit else None


def _side_of(action: str) -> SignalType:
    return SignalType.SELL if str(action).upper() == "SELL" else SignalType.BUY


def _trade_time(trade: IBTrade) -> datetime:
    log = getattr(trade, "log", None) or ()
    stamp = getattr(log[0], "time", None) if log else None
    return stamp if isinstance(stamp, datetime) else datetime.now(timezone.utc)
