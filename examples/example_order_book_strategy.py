"""
An order-book strategy end to end: synthetic L2 books and prints -> OrderBookStrategy ->
RiskManager -> SimBroker -> BasicPortfolio, all through the ordinary ExecutionSystem.

Run:  python examples/example_order_book_strategy.py
"""
import asyncio
import random
from datetime import datetime, timedelta, timezone

from ghosalquant import OrderBookStrategy
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import BookLevel, Fill, Liquidity, OrderBook, PublicTrade, Signal, SignalType
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.data_layer.merge import merge_datasources
from ghosalquant.execution.context import StrategyContext
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.risk.rules.rule_max_open_positions import MaxOpenPositions
from ghosalquant.util.backtester_utils import calculate_kpms


class JoinTheBidStrategy(OrderBookStrategy):
    """
    When flat with nothing resting, join the best bid (a maker order). When the bid fills, offer
    the position two ticks higher. One position at a time; the resting order waits behind the
    quantity already displayed at its price and fills when public prints trade through it.
    """

    def __init__(self, quantity: float = 10.0, tick: float = 0.01, exit_ticks: int = 2) -> None:
        super().__init__()
        self.quantity = quantity
        self.tick = tick
        self.exit_ticks = exit_ticks
        self.fills: list[Fill] = []

    def on_start(self) -> None:
        print("JoinTheBidStrategy started")

    def on_end(self) -> None:
        print(f"JoinTheBidStrategy finished with {len(self.fills)} fills")

    def on_order_book(self, book: OrderBook, context: StrategyContext):
        if not book.is_two_sided():
            return None

        if context.position_quantity == 0.0 and not context.has_open_orders:
            price = book.best_bid.price
            return Signal(
                time_stamp=book.time_stamp,
                symbol=book.symbol,
                signal_type=SignalType.BUY,
                ref_price=price,
                quantity=self.quantity,
                limit_price=price,
                liquidity=Liquidity.MAKER,
                metadata={metadata_keys.REASON: "join_bid"},
            )

        if context.position_quantity > 0.0 and context.open_sell_quantity == 0.0:
            price = round((context.average_entry_price or book.best_ask.price) + self.exit_ticks * self.tick, 2)
            return Signal(
                time_stamp=book.time_stamp,
                symbol=book.symbol,
                signal_type=SignalType.SELL,
                ref_price=price,
                quantity=context.position_quantity,
                limit_price=price,
                liquidity=Liquidity.MAKER,
                metadata={metadata_keys.REASON: "take_profit"},
            )
        return None

    def on_public_trade(self, trade: PublicTrade, context: StrategyContext):
        return None

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)


def build_events(seed: int = 7, steps: int = 600, symbol: str = "SIM") -> list:
    """A random-walk mid with a five-level book each second and an occasional print."""
    rng = random.Random(seed)
    start = datetime(2025, 1, 6, 14, 30, tzinfo=timezone.utc)
    mid = 100.0
    events: list = []
    for step in range(steps):
        mid = max(1.0, mid + rng.gauss(0.0, 0.03))
        half_spread = 0.01 * rng.choice([1, 1, 1, 2])
        bid = round(mid - half_spread, 2)
        ask = round(mid + half_spread, 2)
        bids = tuple(BookLevel(round(bid - 0.01 * i, 2), float(rng.randint(5, 40))) for i in range(5))
        asks = tuple(BookLevel(round(ask + 0.01 * i, 2), float(rng.randint(5, 40))) for i in range(5))
        time_stamp = start + timedelta(seconds=step)
        events.append(OrderBook(time_stamp=time_stamp, symbol=symbol, bids=bids, asks=asks, sequence=step))
        if rng.random() < 0.3:
            side = rng.choice([SignalType.BUY, SignalType.SELL])
            price = ask if side is SignalType.BUY else bid
            events.append(PublicTrade(
                time_stamp=time_stamp + timedelta(milliseconds=500),
                symbol=symbol,
                price=price,
                quantity=float(rng.randint(1, 10)),
                aggressor=side,
            ))
    return events


def run(seed: int = 7, steps: int = 600):
    events = build_events(seed=seed, steps=steps)
    books = [event for event in events if isinstance(event, OrderBook)]
    prints = [event for event in events if isinstance(event, PublicTrade)]

    strategy = JoinTheBidStrategy()
    portfolio = BasicPortfolio(init_capital=10_000.0)
    execution = ExecutionSystem(
        data_src=merge_datasources(ListDataSource(books), ListDataSource(prints)),
        strategy=strategy,
        risk_manager=RiskManager(rules=[MaxOpenPositions(max_open_positions=1)]),
        portfolio=portfolio,
        broker=OrderBookSimBroker(maker_rebate_bps=2.0, taker_fee_bps=5.0, public_trade_participation=0.5),
        snapshot_interval=timedelta(seconds=5),
    )
    asyncio.run(execution.run())
    return strategy, portfolio, calculate_kpms(portfolio)


if __name__ == "__main__":
    strategy, portfolio, kpms = run()
    print(f"\nKEY PERFORMANCE METRICS:\n{kpms}\n")
    print("(annualised figures extrapolate a ten-minute synthetic sample, so they are huge by construction)")
    print(f"Equity points: {len(list(portfolio.equity_curve))}, fills: {len(strategy.fills)}")
    print(portfolio)
