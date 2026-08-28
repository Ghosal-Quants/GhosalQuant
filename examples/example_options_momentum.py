"""
An options strategy end to end: underlier bars and a chain's NBBO -> ContractRegistry ->
RiskManager -> OrderBookSimBroker -> BasicPortfolio, through the ordinary ExecutionSystem.

The underlier decides and the option prices: pricing off the last quote of a contract you are not
trading sends a limit to where the market was, which in a fast move never fills.

Quotes are Black-Scholes at a fixed vol, standing in for a recorded chain. Real backtests use
recorded NBBO, because the surface is the strategy.

Run:  python examples/example_options_momentum.py
"""
import asyncio
import math
import random
import statistics
from datetime import date, datetime, time, timedelta, timezone

from ghosalquant import OrderBookStrategy
from ghosalquant.brokerage.order_book_sim import OrderBookSimBroker
from ghosalquant.core.contracts import Contract, ContractRegistry, Right
from ghosalquant.core.types import Bar, BookLevel, Fill, Liquidity, OrderBook, Signal, SignalType
from ghosalquant.data_layer.list_datasource import ListDataSource
from ghosalquant.execution.context import StrategyContext
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.portfolio.marks import MarkPolicy
from ghosalquant.risk import Window
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.risk.rules.rule_integer_quantity import IntegerQuantity
from ghosalquant.risk.rules.rule_max_symbol_exposure import MaxSymbolExposure
from ghosalquant.util.backtester_utils import calculate_kpms

UNDERLIER = "AAPL"
MULTIPLIER = 100.0
VOLATILITY = 0.28
CAPITAL = 100_000.0

SESSIONS = (date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4), date(2026, 3, 5))
EXPIRIES = (date(2026, 3, 4), date(2026, 3, 11))
STRIKES = tuple(132.0 + 2.0 * step for step in range(17))
# Only the strikes near the money are quoted, as a real subscription would be.
CHAIN_WIDTH = 8.0
BAR_MINUTES = 5
BARS_PER_SESSION = 78
# The close, in the timezone the events are stamped in.
CLOSE = timedelta(hours=21)


def occ_symbol(underlier: str, expiry: date, right: Right, strike: float) -> str:
    """The 21-character OCC symbol the US option venues quote in."""
    letter = "C" if right is Right.CALL else "P"
    return f"{underlier:<6}{expiry:%y%m%d}{letter}{round(strike * 1000):08d}"


def build_chain() -> list[Contract]:
    return [
        Contract.from_occ(occ_symbol(UNDERLIER, expiry, right, strike), multiplier=MULTIPLIER)
        for expiry in EXPIRIES
        for right in (Right.CALL, Right.PUT)
        for strike in STRIKES
    ]


class MomentumOptionBuyer(OrderBookStrategy):
    """
    Buy directional premium once a fast move is already underway, and give it a short leash.

    Entry needs ``entry_z`` standard deviations of recent ``window``-bar moves, and ``min_move``
    outright: a fixed percentage is a different strategy on every name and in every regime.

    The contract is the nearest expiry with ``min_days_to_expiry`` left, the first strike
    ``otm_fraction`` beyond the money, and must clear ``max_spread`` and ``min_premium_fraction``
    of spot — below that it is a lottery ticket whose edge goes to spread and theta.
    """

    def __init__(
        self,
        contracts: ContractRegistry,
        *,
        window: int = 6,
        entry_z: float = 2.5,
        min_move: float = 0.004,
        vol_lookback: int = 120,
        vol_ramp: int = 60,
        target: float = 0.5,
        stop: float = -0.35,
        max_hold_minutes: int = 240,
        cooldown_minutes: int = 30,
        otm_fraction: float = 0.01,
        min_premium_fraction: float = 0.005,
        min_days_to_expiry: int = 1,
        max_spread: float = 0.08,
        risk_budget: float = 2_500.0,
        close_before_expiry: bool = True,
    ) -> None:
        super().__init__()
        self.contracts = contracts
        self.window = window
        self.entry_z = entry_z
        self.min_move = min_move
        self.vol = Window(vol_lookback, ramp_up=vol_ramp)
        self.target = target
        self.stop = stop
        self.max_hold = timedelta(minutes=max_hold_minutes)
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.otm_fraction = otm_fraction
        self.min_premium_fraction = min_premium_fraction
        self.min_days_to_expiry = min_days_to_expiry
        self.max_spread = max_spread
        self.risk_budget = risk_budget
        self.close_before_expiry = close_before_expiry

        self.closes: list[float] = []
        self.moves: list[float] = []
        self.spot = 0.0
        self.move = 0.0
        # No opinion until the distribution of moves is known, rather than an opinion of zero.
        self.trigger = math.inf
        self.quotes: dict[str, OrderBook] = {}
        self.held_symbol: str | None = None
        self.held = 0.0
        self.entry_price: float | None = None
        self.entered_at: datetime | None = None
        self.flat_since: datetime | None = None
        self.fills: list[Fill] = []
        self.skipped_on_spread = 0
        self.skipped_on_premium = 0

    def on_start(self) -> None:
        print(f"MomentumOptionBuyer started over {len(self.contracts.contracts)} contracts")

    def on_end(self) -> None:
        print(f"MomentumOptionBuyer finished with {len(self.fills)} fills")

    def on_market_data(self, bar: Bar) -> None:
        """The underlier arms the trade. It never prices it, because a bar is not a quote."""
        if bar.symbol != UNDERLIER:
            return None
        self.spot = bar.close
        self.closes.append(bar.close)
        if len(self.closes) <= self.window:
            return None
        self.move = self.closes[-1] / self.closes[-1 - self.window] - 1.0
        self.moves.append(self.move)

        recent = self.vol.of(self.moves)
        if recent is None:
            self.trigger = math.inf      # no opinion until the distribution is known
        else:
            self.trigger = max(self.min_move, self.entry_z * statistics.pstdev(recent))
        return None

    def on_order_book(self, book: OrderBook, context: StrategyContext):
        if book.symbol == UNDERLIER or not book.is_two_sided():
            return None
        self.quotes[book.symbol] = book

        if self.held > 0.0:
            return self._exit(book) if book.symbol == self.held_symbol else None
        return self._entry(book)

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)
        if fill.symbol == UNDERLIER:
            return None
        if fill.side is SignalType.BUY:
            self.held_symbol = fill.symbol
            self.held += fill.quantity
            self.entry_price = fill.price
            self.entered_at = fill.time_stamp
        else:
            self.held -= fill.quantity
            if self.held <= 0.0:
                self.held_symbol, self.entry_price, self.entered_at = None, None, None
                self.flat_since = fill.time_stamp
        return None

    # ----- Entry ----- #

    def _entry(self, book: OrderBook):
        at = book.time_stamp
        if abs(self.move) < self.trigger or self.spot <= 0.0:
            return None
        if self.flat_since is not None and at - self.flat_since < self.cooldown:
            return None

        wanted = self._target(at, up=self.move > 0.0)
        if wanted is None or wanted.symbol != book.symbol:
            # Wait for its own quote rather than pricing off the last one.
            return None

        ask = book.best_ask.price
        quantity = math.floor(self.risk_budget / (ask * wanted.multiplier))
        if quantity < 1:
            return None
        self.move = 0.0
        return self._signal(at, book.symbol, SignalType.BUY, float(quantity), ask)

    def _target(self, at: datetime, *, up: bool) -> Contract | None:
        """
        The contract to buy, ranked and screened on the quotes already seen.

        A stale quote is fine for screening; the order is priced from the contract's own live one.
        """
        for contract in self._candidates(at, up=up):
            quote = self.quotes.get(contract.symbol)
            if quote is None or not quote.is_two_sided():
                continue
            bid, ask = quote.best_bid.price, quote.best_ask.price
            if ask < self.min_premium_fraction * self.spot:
                self.skipped_on_premium += 1
                continue
            if (ask - bid) / ((ask + bid) / 2.0) > self.max_spread:
                # Premium has to move the spread before it is worth anything.
                self.skipped_on_spread += 1
                continue
            if math.floor(self.risk_budget / (ask * contract.multiplier)) < 1:
                continue
            return contract
        return None

    def _candidates(self, at: datetime, *, up: bool) -> list[Contract]:
        """Every contract beyond the money, nearest expiry and nearest strike first."""
        right = Right.CALL if up else Right.PUT
        target = self.spot * (1.0 + (self.otm_fraction if up else -self.otm_fraction))
        wanted = []
        for contract in self.contracts.contracts:
            if contract.right is not right or contract.expiry is None:
                continue
            days = (contract.expiry - at.date()).days
            if days < self.min_days_to_expiry:
                continue
            if (contract.strike < target) if up else (contract.strike > target):
                continue
            wanted.append(((days, abs(contract.strike - target)), contract))
        return [contract for _, contract in sorted(wanted, key=lambda item: item[0])]

    # ----- Exit ----- #

    def _exit(self, book: OrderBook):
        symbol = self.held_symbol
        if symbol is None or self.entry_price is None or book.best_bid is None:
            return None

        at = book.time_stamp
        bid = book.best_bid.price
        pnl = bid / self.entry_price - 1.0
        aged = self.entered_at is not None and at - self.entered_at >= self.max_hold
        expiring = self.close_before_expiry and self._near_expiry(symbol, at)
        if pnl >= self.target or pnl <= self.stop or aged or expiring:
            return self._signal(at, symbol, SignalType.SELL, self.held, bid)
        return None

    def _near_expiry(self, symbol: str, at: datetime) -> bool:
        """Assignment is an operations problem; a directional trade takes its money and leaves."""
        expiry = self.contracts.get(symbol).expiry
        return expiry is not None and at >= _expires_at(expiry, at) - timedelta(minutes=30)

    def _signal(self, at: datetime, symbol: str, side: SignalType, quantity: float, price: float):
        return Signal(
            time_stamp=at,
            symbol=symbol,
            signal_type=side,
            ref_price=price,
            quantity=quantity,
            limit_price=price,
            liquidity=Liquidity.TAKER,
        )


# ----- The market it trades ----- #

def _expires_at(expiry: date, like: datetime) -> datetime:
    return datetime.combine(expiry, time()).replace(tzinfo=like.tzinfo) + CLOSE


def black_scholes(spot: float, contract: Contract, at: datetime, vol: float) -> float:
    """Undiscounted Black-Scholes, which is enough to make a quote move with its underlier."""
    years = max((_expires_at(contract.expiry, at) - at).total_seconds() / (365.0 * 24 * 3600), 0.0)
    call_side = contract.right is Right.CALL
    if years <= 0.0:
        intrinsic = spot - contract.strike if call_side else contract.strike - spot
        return max(intrinsic, 0.0)
    d1 = (math.log(spot / contract.strike) + 0.5 * vol * vol * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    call = spot * _norm_cdf(d1) - contract.strike * _norm_cdf(d2)
    return call if call_side else call - spot + contract.strike


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _drift(day: int, bar: int) -> float:
    """
    Two moves, deliberately different afterwards: the drop runs, the rally fades. One trade
    should reach its target and the other its stop.
    """
    if day == 1:
        if 12 <= bar < 15:
            return -0.032 / 3
        if 15 <= bar < 45:
            return -0.034 / 30
    if day == 2:
        if 12 <= bar < 15:
            return 0.042 / 3
        if 15 <= bar < 40:
            return -0.040 / 25
    return 0.0


def underlier_path(seed: int = 7, bars: int = BARS_PER_SESSION) -> list[tuple[datetime, float]]:
    """A quiet session, then a drop that runs, then a rally that fades."""
    rng = random.Random(seed)
    price = 150.0
    path = []
    for day, session in enumerate(SESSIONS):
        start = datetime(session.year, session.month, session.day, 14, 30, tzinfo=timezone.utc)
        for bar in range(bars):
            price *= 1.0 + _drift(day, bar) + rng.gauss(0.0, 0.0009)
            path.append((start + timedelta(minutes=BAR_MINUTES * bar), round(price, 2)))
    return path


def build_events(seed: int = 7, bars: int = BARS_PER_SESSION,
                 chain: list[Contract] | None = None) -> list:
    """Underlier bars interleaved with the chain's NBBO, one quote per contract per bar."""
    chain = build_chain() if chain is None else chain
    events = []
    for at, price in underlier_path(seed=seed, bars=bars):
        events.append(Bar(
            time_stamp=at, symbol=UNDERLIER,
            open=price, high=price, low=price, close=price, volume=10_000.0,
        ))
        for contract in chain:
            if at.date() > contract.expiry or abs(contract.strike - price) > CHAIN_WIDTH:
                continue
            fair = black_scholes(price, contract, at, VOLATILITY)
            # Wider on the cheap wings, as a real chain is.
            half_spread = max(0.02, fair * 0.02)
            events.append(OrderBook(
                time_stamp=at, symbol=contract.symbol,
                bids=(BookLevel(max(0.01, round(fair - half_spread, 2)), 50.0),),
                asks=(BookLevel(round(fair + half_spread, 2), 50.0),),
            ))
    return events


def run(seed: int = 7, bars: int = BARS_PER_SESSION, **strategy_kwargs):
    chain = build_chain()
    contracts = ContractRegistry(chain)
    strategy = MomentumOptionBuyer(contracts, **strategy_kwargs)
    portfolio = BasicPortfolio(init_capital=CAPITAL, contracts=contracts)
    execution = ExecutionSystem(
        data_src=ListDataSource(build_events(seed=seed, bars=bars, chain=chain)),
        strategy=strategy,
        risk_manager=RiskManager(rules=[
            MaxSymbolExposure(capital=CAPITAL, max_notional_fraction=0.05),
            IntegerQuantity(),
        ]),
        portfolio=portfolio,
        broker=OrderBookSimBroker(maker_rebate_bps=0.0, taker_fee_bps=0.0, seed=1),
        contracts=contracts,
        mark_policy=MarkPolicy.CONSERVATIVE,
    )
    asyncio.run(execution.run())
    return strategy, portfolio, calculate_kpms(portfolio)


if __name__ == "__main__":
    strategy, portfolio, kpms = run()
    print(f"\nKEY PERFORMANCE METRICS:\n{kpms}\n")
    for fill in strategy.fills:
        print(f"  {fill.time_stamp:%m-%d %H:%M}  {fill.side.value:4} {fill.quantity:>3g} "
              f"{fill.symbol}  @ {fill.price:.2f}")
    print(f"\nQuotes refused on spread: {strategy.skipped_on_spread}")
    print(f"Realised {portfolio.realised_pnl:,.2f} on {CAPITAL:,.0f}, "
          f"equity {portfolio.current_equity:,.2f}")
