import asyncio
import logging
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from ..core import metadata_keys
from ..core.contracts import ContractRegistry, settlement_fills
from ..core.types import (
    Bar,
    CashFlow,
    Fill,
    MarketEvent,
    OpenOrderView,
    Order,
    OrderBook,
    PublicTrade,
    Signal,
    SignalType,
)
from ..data_layer.datasource_base import DataSource
from ..execution.context import StrategyContext
from ..execution.strategy_base import Strategy, as_signal_list
from ..execution.sinks import SignalSink
from ..risk.risk_manager import RiskManager
from ..risk.types import RiskContext
from ..portfolio.portfolio_base import Portfolio
from ..portfolio.marks import MarkPolicy, mark_from_book, mark_from_trade
from ..brokerage.broker_base import Broker
from ..brokerage.open_orders import OpenOrderQueries
from ..state.event_store import EventStore

logger = logging.getLogger(__name__)

# US equity options stop trading at 16:00 New York, which is 21:00 UTC outside daylight saving.
DEFAULT_SETTLEMENT_TIME = time(21, 0)

# A replacement held longer than this is a stale decision, so it is dropped.
REPLACEMENT_MAX_WAIT = timedelta(seconds=30)


@dataclass(slots=True)
class _Replacement:
    """Orders held back until the resting orders they replace have gone."""
    cancelling: set[str]
    orders: list[Order]


class _LazyContexts:
    """
    The risk and strategy contexts for one event, built on first use and shared by every signal
    that event produces. One order book routinely yields several signals; building the contexts
    per signal would make them the hot path.
    """

    __slots__ = ("_system", "_symbol", "_risk", "_strategy", "replaced")

    def __init__(self, system: "ExecutionSystem", symbol: str) -> None:
        self._system = system
        self._symbol = symbol
        self._risk: RiskContext | None = None
        self._strategy: StrategyContext | None = None
        self.replaced: set[tuple[str, SignalType]] = set()

    def invalidate(self) -> None:
        self._risk = None
        self._strategy = None

    @property
    def risk(self) -> RiskContext:
        if self._risk is None:
            self._risk = self._system._build_risk_context()
        return self._risk

    @property
    def strategy(self) -> StrategyContext:
        if self._strategy is None:
            self._strategy = self._system._build_strategy_context(self._symbol, self.risk)
        return self._strategy


class ExecutionSystem:
    def __init__(
            self,
            data_src: DataSource,
            strategy: Strategy,
            signal_destination: SignalSink | None = None,
            risk_manager: RiskManager | None = None,
            portfolio: Portfolio | None = None,
            broker: Broker | None = None,
            *,
            mark_policy: MarkPolicy = MarkPolicy.MID,
            snapshot_interval: timedelta = timedelta(seconds=1),
            journal: EventStore | None = None,
            contracts: ContractRegistry | None = None,
            settlement_time: time = DEFAULT_SETTLEMENT_TIME,
    ) -> None:

        self.data_src = data_src
        self.strategy = strategy

        self.sig_sink = signal_destination
        self.risk_manager = risk_manager
        self.portfolio = portfolio
        self.broker = broker
        self.mark_policy = mark_policy
        self.snapshot_interval = snapshot_interval
        self.journal = journal
        self.contracts = contracts if contracts is not None else ContractRegistry()
        self.settlement_time = settlement_time

        self._price_history: dict[str, list[float]] = {}
        self._last_snapshot_at: datetime | None = None
        self._last_snapshot_version: int | None = None
        self._last_event_time: datetime | None = None
        self._replacements: dict[tuple[str, SignalType], _Replacement] = {}

        if self.portfolio is not None and self.risk_manager is None:
            raise ValueError("A RiskManager has to be used in combination with a Portfolio.")

        if self.portfolio is not None and self.broker is None:
            raise ValueError("A Broker has to be used in combination with a Portfolio.")

    async def run(self) -> None:
        if self.broker:
            await self.broker.connect()

        self.strategy.on_start()

        try:
            async for raw in self.data_src:
                await self._expire_due(raw.time_stamp)

                event: MarketEvent = raw
                self._last_event_time = raw.time_stamp
                if self.broker:
                    event = self.broker.on_market_event(raw)

                # Mark from the raw event, never from the broker's adjusted view.
                if self.portfolio:
                    self._mark(raw)

                # price_history stays a bar series; the risk rules read it as one.
                if isinstance(raw, Bar):
                    self._record_price(raw)

                # Collect fills from the broker and pass to portfolio
                if self.broker and self.portfolio:
                    await self._apply_fills(self.broker.get_fills())
                    await self._apply_cash_flows(self.broker.get_cash_flows())

                # Settle last: the position and the underlier price both have to be current.
                if self.portfolio:
                    await self._settle_expiries(raw.time_stamp)

                self._send_replacements(raw.time_stamp)

                contexts = _LazyContexts(self, raw.symbol)

                for signal in self._signals_for(event, contexts):
                    await self._emit(signal, contexts)

            self._flush_equity()

        except (KeyboardInterrupt, asyncio.CancelledError):
            self._flush_equity()
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

    # ----- Fills ----- #

    async def _apply_fills(self, fills: list[Fill]) -> None:
        """
        Apply fills to the portfolio, journalling them first when a journal is present.

        The journal write and the portfolio update commit together, so on disk "journalled" and
        "applied" mean the same thing. A process that dies partway through rolls the journal back
        and reapplies the fill on the next run; one that dies after committing skips it, because
        the journal already refuses an execution it holds.
        """
        if not fills:
            return
        for fill in fills:
            if self.journal is not None and not self.journal.record(
                "fill", _fill_payload(fill), observed_at=fill.time_stamp
            ):
                continue
            await self.portfolio.process_fill(fill)
            self._note_snapshot(fill.time_stamp)
            self._drop_replacement_after(fill)
            self._call_hook("on_fill", fill)
        if self.journal is not None:
            self.journal.commit()

    async def _apply_cash_flows(self, flows: list[CashFlow]) -> None:
        """Book the broker's cash flows as ``_apply_fills`` applies fills, journalled first."""
        if not flows:
            return
        for flow in flows:
            if self.journal is not None and not self.journal.record(
                "cash_flow", _cash_flow_payload(flow), observed_at=flow.time_stamp
            ):
                continue
            await self.portfolio.process_cash_flow(flow)
            self._note_snapshot(flow.time_stamp)
        if self.journal is not None:
            self.journal.commit()

    async def _settle_expiries(self, until: datetime) -> None:
        """
        Settle contracts whose expiry has passed, against the underlier's last known price.

        Settlement arrives as fills, so it is accounted, journalled and reported to the strategy
        the same way a broker execution is.
        """
        if self.portfolio is None:
            return
        prices = self.portfolio.last_prices
        for symbol, quantity in list(self.portfolio.open_qty_by_symbol.items()):
            contract = self.contracts.get(symbol)
            if contract.expiry is None or not self._expired(contract.expiry, until):
                continue
            price = prices.get(contract.underlier)
            if price is None:
                logger.warning("Cannot settle %s: no price for %s", symbol, contract.underlier)
                continue
            await self._apply_fills(settlement_fills(contract, quantity, price, at=until))

    def _expired(self, expiry: date, until: datetime) -> bool:
        """
        Whether an expiry has passed by ``until``.

        A contract trades until the close, so settling on the day's first event would take the
        session away from the strategy. The date having advanced settles it too, for data that
        stops before ``settlement_time``.
        """
        if until.date() > expiry:
            return True
        closes_at = datetime.combine(expiry, self.settlement_time)
        if until.tzinfo is not None:
            closes_at = closes_at.replace(tzinfo=until.tzinfo)
        return until >= closes_at

    # ----- Event routing ----- #

    async def _emit(self, signal: Signal, contexts: _LazyContexts) -> None:
        if self.sig_sink is not None:
            await self.sig_sink.on_signal(signal)
        if self.risk_manager is None:
            return
        order: Order | None = self.risk_manager.evaluate(signal, contexts.risk)
        if order is None or self.broker is None:
            return
        if not self._held_for_replacement(order, contexts):
            self._submit(order)

    def _submit(self, order: Order) -> None:
        broker_order_id = self.broker.submit(order)
        self._call_hook("on_order_accepted", order, broker_order_id)

    def _held_for_replacement(self, order: Order, contexts: _LazyContexts) -> bool:
        """Cancel the resting orders ``order`` replaces, holding it while the venue still works one."""
        metadata = order.metadata or {}
        replaces = metadata.get(metadata_keys.REPLACE_ENTRY_ORDERS) or metadata.get(metadata_keys.REPLACE_EXIT_ORDERS)
        if not replaces or not isinstance(self.broker, OpenOrderQueries):
            return False

        key = (order.symbol, order.side)
        # Only an event's first order for a side cancels, so a ladder keeps all its rungs.
        if key not in contexts.replaced:
            contexts.replaced.add(key)
            self._replacements.pop(key, None)
            self.broker.cancel_open_orders(symbol=order.symbol, side=order.side)
            contexts.invalidate()
            still_working = self._resting_ids(key)
            if still_working:
                self._replacements[key] = _Replacement(still_working, [])

        replacement = self._replacements.get(key)
        if replacement is None:
            return False
        replacement.orders.append(order)
        return True

    def _send_replacements(self, now: datetime) -> None:
        for key, replacement in list(self._replacements.items()):
            if now - replacement.orders[0].time_stamp >= REPLACEMENT_MAX_WAIT:
                logger.warning("Dropping the replacement for %s %s: its cancel is unconfirmed", key[0], key[1].value)
                del self._replacements[key]
                continue
            replacement.cancelling &= self._resting_ids(key)
            if not replacement.cancelling:
                del self._replacements[key]
                for order in replacement.orders:
                    self._submit(order)

    def _drop_replacement_after(self, fill: Fill) -> None:
        """A replaced order that fills changes the position the held order was decided on."""
        key = (fill.symbol, fill.side)
        replacement = self._replacements.get(key)
        if replacement is not None and fill.broker_order_id in replacement.cancelling:
            logger.info("Dropping the replacement for %s %s: a replaced order filled", key[0], key[1].value)
            del self._replacements[key]

    def _resting_ids(self, key: tuple[str, SignalType]) -> set[str]:
        symbol, side = key
        return {view.broker_order_id for view in self._open_order_views(symbol) if view.side is side}

    async def _expire_due(self, until: datetime) -> None:
        """
        Expire resting orders whose time to live ran out before ``until``.

        A broker only reaches a symbol when that symbol has an event, so without this an order
        stays in the open-order state long after it expired, and any strategy or risk rule that
        asks what is resting is told about an order the venue would already have cancelled.
        """
        if self.broker is None:
            return
        # Only orders resting when the advance began are announced: a replacement carrying a stale
        # timestamp is due the moment it rests, and announcing it would loop.
        announce = set(self._open_orders_by_id())
        while True:
            expires_at = self.broker.next_order_expiry_at()
            if expires_at is None or expires_at > until:
                return
            resting = self._open_orders_by_id()
            expired = self.broker.expire_orders(now=expires_at)
            if not expired:
                return
            for order_id in expired:
                order = resting.get(order_id)
                if order is None or order_id not in announce:
                    continue
                contexts = _LazyContexts(self, order.symbol)
                hook = getattr(self.strategy, "on_order_expired", None)
                result = hook(order, order_id, expires_at, contexts.strategy) if callable(hook) else None
                for signal in as_signal_list(result):
                    await self._emit(signal, contexts)

    def _open_orders_by_id(self) -> dict[str, Order]:
        views = getattr(self.broker, "open_order_views", None)
        return {view.broker_order_id: view.order for view in views()} if callable(views) else {}

    def _signals_for(self, event: MarketEvent, contexts: _LazyContexts) -> list[Signal]:
        match event:
            case Bar():
                result = self.strategy.on_market_data(event)
            case OrderBook():
                hook = getattr(self.strategy, "on_order_book", None)
                result = hook(event, contexts.strategy) if callable(hook) else None
            case PublicTrade():
                hook = getattr(self.strategy, "on_public_trade", None)
                result = hook(event, contexts.strategy) if callable(hook) else None
            case _:
                raise TypeError(f"Unsupported market event: {type(event).__name__}")
        return as_signal_list(result)

    def _call_hook(self, name: str, *args) -> None:
        # Strategies may be duck-typed without the lifecycle hooks.
        hook = getattr(self.strategy, name, None)
        if callable(hook):
            hook(*args)

    # ----- Marking ----- #

    def _mark(self, raw: MarketEvent) -> None:
        portfolio = self.portfolio
        if isinstance(raw, Bar):
            portfolio.update_equity(raw)
            self._note_snapshot(raw.time_stamp)
            return

        symbol = raw.symbol
        previous = portfolio.last_prices.get(symbol)
        position = portfolio.open_qty_by_symbol.get(symbol, 0.0)
        if isinstance(raw, OrderBook):
            if not raw.is_two_sided():
                return
            price = mark_from_book(raw, previous_mark=previous, position_quantity=position, policy=self.mark_policy)
        else:
            price = mark_from_trade(raw, previous_mark=previous, position_quantity=position, policy=self.mark_policy)
        if price is None:
            return

        portfolio.mark(symbol, price, raw.time_stamp)
        self._maybe_snapshot(raw.time_stamp)

    def _flush_equity(self) -> None:
        """
        Bring the equity curve up to date with the last marks seen.

        Between bars and fills a snapshot is only taken every ``snapshot_interval``, so without
        this the final equity point (and with it ``current_equity``, ``final_balance`` and the
        unrealised KPM) could lag the last event by up to that interval.
        """
        if self.portfolio is None or self._last_event_time is None:
            return
        prices = self.portfolio.last_prices
        if any(symbol not in prices for symbol in self.portfolio.open_qty_by_symbol):
            return
        self.portfolio.snapshot_equity(time_stamp=self._last_event_time, prices=prices)
        self._note_snapshot(self._last_event_time)

    def _maybe_snapshot(self, time_stamp: datetime) -> None:
        version = self._accounting_version()
        due = (
            self._last_snapshot_at is None
            or time_stamp - self._last_snapshot_at >= self.snapshot_interval
            or version != self._last_snapshot_version
        )
        if not due:
            return
        self.portfolio.snapshot_equity(time_stamp=time_stamp, prices=self.portfolio.last_prices)
        self._note_snapshot(time_stamp)

    def _note_snapshot(self, time_stamp: datetime) -> None:
        self._last_snapshot_at = time_stamp
        self._last_snapshot_version = self._accounting_version()

    def _accounting_version(self) -> int | None:
        return getattr(self.portfolio, "accounting_version", None)

    def _record_price(self, bar: Bar) -> None:
        self._price_history.setdefault(bar.symbol, []).append(float(bar.close))

    # ----- Contexts ----- #

    def _open_order_views(self, symbol: str | None = None) -> tuple[OpenOrderView, ...]:
        if isinstance(self.broker, OpenOrderQueries):
            return tuple(self.broker.open_order_views(symbol))
        return ()

    def _build_strategy_context(self, symbol: str, risk: RiskContext) -> StrategyContext:
        position_quantity = 0.0
        average_entry_price: float | None = None
        cash = 0.0

        if self.portfolio is not None:
            position_quantity = float(self.portfolio.open_qty_by_symbol.get(symbol, 0.0))
            average_entry_price = self.portfolio.average_entry_price(symbol)
            cash = float(self.portfolio.cash_balance)

        return StrategyContext(
            symbol=symbol,
            contract=self.contracts.get(symbol),
            position_quantity=position_quantity,
            average_entry_price=average_entry_price,
            cash=cash,
            open_orders=tuple(view for view in risk.open_orders if view.symbol == symbol),
            risk=risk,
        )

    def _build_risk_context(self) -> RiskContext:
        open_positions = 0
        open_qty_by_symbol: dict[str, float] = {}
        position_cost_by_symbol: dict[str, float] = {}
        peak_equity: float | None = None
        equity: float | None = None
        current_drawdown: float | None = None

        if self.portfolio is not None:
            open_positions = int(self.portfolio.open_position_count)
            open_qty_by_symbol = dict(self.portfolio.open_qty_by_symbol)
            position_cost_by_symbol = {
                symbol: sum(
                    float(trade.entry_price) * float(trade.size)
                    for trade in trades.values()
                )
                for symbol, trades in getattr(
                    self.portfolio,
                    "_open_trades",
                    {},
                ).items()
                if trades
            }
            peak_equity = float(self.portfolio.peak_equity)
            equity = float(self.portfolio.current_equity)

            if peak_equity > 0.0:
                current_drawdown = (equity / peak_equity) - 1.0

        return RiskContext(
            open_positions=open_positions,
            open_qty_by_symbol=open_qty_by_symbol,
            position_cost_by_symbol=position_cost_by_symbol,
            current_drawdown=current_drawdown,
            peak_equity=peak_equity,
            current_equity=equity,
            price_history=(
                {symbol: list(prices) for symbol, prices in self._price_history.items()}
                if self._price_history else None
            ),
            open_orders=self._open_order_views(),
            contracts=self.contracts,
        )


def _cash_flow_payload(flow: CashFlow) -> dict:
    return {
        "time_stamp": flow.time_stamp.isoformat(),
        "symbol": flow.symbol,
        "amount": float(flow.amount),
        "reason": flow.reason,
    }


def _fill_payload(fill: Fill) -> dict:
    """What a journalled fill records. The execution id is what makes it unique."""
    return {
        "time_stamp": fill.time_stamp.isoformat(),
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": float(fill.quantity),
        "price": float(fill.price),
        "execution_id": fill.execution_id,
        "broker_order_id": fill.broker_order_id,
        "commission": fill.commission,
    }
