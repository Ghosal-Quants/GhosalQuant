"""A scripted IB: ib_insync's real IB, Trade and wrapper, with only the socket calls stubbed."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ib_insync import Execution

from ghosalquant.brokerage.ibkr import IBKRBroker
from ghosalquant.core.types import BookLevel, Liquidity, OrderBook, Signal, SignalType
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.execution.strategy_base import OrderBookStrategy
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk.risk_manager import RiskManager

T0 = datetime(2026, 6, 1, 14, 30, tzinfo=timezone.utc)
CLIENT_ID = 1


class Venue:
    """Answers a broker the way IB does. ``confirms_cancels=False`` leaves cancels pending until told."""

    def __init__(self, broker: IBKRBroker, *, confirms_cancels: bool = False) -> None:
        self.broker = broker
        self.ib = broker.ib
        self.confirms_cancels = confirms_cancels
        self.placed: list = []
        self.cancels: list[int] = []
        self.connected = False
        self._next_order_id = 100
        self._next_exec_id = 0

        self.ib.wrapper.clientId = CLIENT_ID
        self.ib.wrapper.lastTime = T0
        self.ib.client.isReady = lambda: self.connected
        self.ib.client.getReqId = self._order_id
        self.ib.client.placeOrder = lambda *args: None
        self.ib.client.cancelOrder = self._on_cancel
        self.ib.connectAsync = self._connect
        self.ib.disconnect = self._disconnect
        self._place = self.ib.placeOrder
        self.ib.placeOrder = self._place_order
        asyncio.run(broker.connect())

    async def _connect(self, host=None, port=None, clientId=None) -> None:
        self.connected = True

    def _disconnect(self) -> None:
        self.connected = False

    def _order_id(self) -> int:
        self._next_order_id += 1
        return self._next_order_id

    def _place_order(self, contract, order):
        trade = self._place(contract, order)
        self.placed.append(order)
        if order.tif == "IOC":
            self.status(order.orderId, "Cancelled", 0.0, order.totalQuantity)
        return trade

    def _on_cancel(self, order_id: int, manual_cancel_time: str = "") -> None:
        self.cancels.append(order_id)
        if self.confirms_cancels:
            self.confirm_cancel(order_id)

    def trade(self, order_id: int):
        return self.ib.wrapper.trades[(CLIENT_ID, order_id)]

    def status(self, order_id: int, status: str, filled: float, remaining: float) -> None:
        self.ib.wrapper.orderStatus(order_id, status, filled, remaining, 0.0, 0, 0, 0.0, CLIENT_ID, "", 0.0)

    def confirm_cancel(self, order_id: int) -> None:
        trade = self.trade(order_id)
        self.status(order_id, "Cancelled", trade.filled(), trade.remaining())

    def execute(self, order_id: int, shares: float, price: float = 100.0, *, last_liquidity: int = 1) -> None:
        self._next_exec_id += 1
        trade = self.trade(order_id)
        self.ib.wrapper.execDetails(-1, trade.contract, Execution(
            execId=f"E{self._next_exec_id}", clientId=CLIENT_ID, orderId=order_id, shares=shares, price=price,
            side="BOT" if trade.order.action == "BUY" else "SLD", lastLiquidity=last_liquidity,
        ))


def book(seconds: int, symbol: str = "AAPL", bid: float = 100.0, ask: float = 100.02) -> OrderBook:
    return OrderBook(
        time_stamp=T0 + timedelta(seconds=seconds), symbol=symbol,
        bids=(BookLevel(bid, 500.0),), asks=(BookLevel(ask, 500.0),),
    )


@dataclass(frozen=True)
class Quote:
    side: SignalType
    price: float
    quantity: float = 10.0
    liquidity: Liquidity | None = None
    metadata: dict = field(default_factory=dict)


class ScriptedStrategy(OrderBookStrategy):
    """Sends the planned quotes on AAPL books and records what it is told."""

    def __init__(self, plan: dict[int, list[Quote]]) -> None:
        super().__init__()
        self.plan = plan
        self.resting: dict[int, list] = {}
        self.accepted: list = []
        self.expired: list = []

    def on_start(self) -> None: ...

    def on_end(self) -> None: ...

    def on_order_book(self, book, context=None):
        if book.symbol != "AAPL":
            return None
        second = round((book.time_stamp - T0).total_seconds())
        self.resting[second] = sorted(
            (view.side.value, view.limit_price, view.remaining_quantity) for view in context.open_orders
        )
        return [
            Signal(
                time_stamp=book.time_stamp, symbol="AAPL", signal_type=quote.side, ref_price=quote.price,
                confidence=1.0, quantity=quote.quantity, limit_price=quote.price, liquidity=quote.liquidity,
                metadata=dict(quote.metadata),
            )
            for quote in self.plan.get(second, ())
        ]

    def on_order_accepted(self, order, broker_order_id) -> None:
        self.accepted.append((order.side.value, order.limit_price))

    def on_order_expired(self, order, broker_order_id, expired_at, context):
        self.expired.append((order.side.value, order.limit_price, expired_at))


class _Stream:
    """Runs ``before`` ahead of each event, the way IB's messages arrive between market events."""

    def __init__(self, events, before) -> None:
        self.events = events
        self.before = before

    async def __aiter__(self):
        for event in self.events:
            if self.before is not None:
                self.before(event)
            yield event


def run(strategy, broker, events, *, before=None, risk_manager=None) -> BasicPortfolio:
    portfolio = BasicPortfolio(init_capital=100_000.0)
    asyncio.run(ExecutionSystem(
        _Stream(events, before), strategy, portfolio=portfolio,
        risk_manager=risk_manager if risk_manager is not None else RiskManager(rules=[]), broker=broker,
    ).run())
    return portfolio


def at_second(event) -> int:
    return round((event.time_stamp - T0).total_seconds())
