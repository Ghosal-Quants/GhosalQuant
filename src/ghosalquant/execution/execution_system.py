import asyncio
import sys
from datetime import datetime, timedelta

from ..core.types import Bar, MarketEvent, OpenOrderView, Order, OrderBook, PublicTrade, Signal
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


class _LazyContexts:
    """
    The risk and strategy contexts for one event, built on first use and shared by every signal
    that event produces. One order book routinely yields several signals; building the contexts
    per signal would make them the hot path.
    """

    __slots__ = ("_system", "_symbol", "_risk", "_strategy")

    def __init__(self, system: "ExecutionSystem", symbol: str) -> None:
        self._system = system
        self._symbol = symbol
        self._risk: RiskContext | None = None
        self._strategy: StrategyContext | None = None

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
    ) -> None:

        self.data_src = data_src
        self.strategy = strategy

        self.sig_sink = signal_destination
        self.risk_manager = risk_manager
        self.portfolio = portfolio
        self.broker = broker
        self.mark_policy = mark_policy
        self.snapshot_interval = snapshot_interval

        self._price_history: dict[str, list[float]] = {}
        self._last_snapshot_at: datetime | None = None
        self._last_snapshot_version: int | None = None
        self._last_event_time: datetime | None = None

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
                    for fill in self.broker.get_fills():
                        await self.portfolio.process_fill(fill)
                        self._note_snapshot(fill.time_stamp)
                        self._call_hook("on_fill", fill)

                contexts = _LazyContexts(self, raw.symbol)

                for signal in self._signals_for(event, contexts):
                    # Forward to optional signal sink
                    if self.sig_sink is not None:
                        await self.sig_sink.on_signal(signal)

                    if self.risk_manager:
                        order: Order | None = self.risk_manager.evaluate(signal, contexts.risk)

                        if order is None:
                            continue

                        if self.broker:
                            broker_order_id = self.broker.submit(order)
                            self._call_hook("on_order_accepted", order, broker_order_id)

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

    # ----- Event routing ----- #

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
            current_equity = float(self.portfolio.current_equity)

            if peak_equity > 0.0:
                current_drawdown = (current_equity / peak_equity) - 1.0

        return RiskContext(
            open_positions=open_positions,
            open_qty_by_symbol=open_qty_by_symbol,
            position_cost_by_symbol=position_cost_by_symbol,
            current_drawdown=current_drawdown,
            peak_equity=peak_equity,
            price_history=(
                {symbol: list(prices) for symbol, prices in self._price_history.items()}
                if self._price_history else None
            ),
            open_orders=self._open_order_views(),
        )
