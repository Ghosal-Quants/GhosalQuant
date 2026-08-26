# System Overview

## Tom's System Notes

### Rough components of system
- Signal generation system
  - Watch market & flag when to trade
- Risk management system
  - Monitor open & proposed positions
  - value at risk? xccy hedge?
- Order management system
  - take an order and split it into multiple orders to be executed at different brokers (or one! although they may not always have volume)
  - Broker (that can simulate matching orders)
- Market data system
  - Collate & manage market data
  - Maybe generate fake data for backtesting as well?
- Testing system
  - Backtesting
  - Forward testing
  - Stats (sharpe, drawdown etc etc)
- Interface
  - Dashboard
  - Strategy interaction
  - Testing interaction
  - Risk interaction

### Version 1 for Backtesting (MVP)
- [x] DataFrame based dumb iterative testing over static data
- [x] Fetch
  - [x] Fetch data from a library
  - [x] Download as DataFrame
- [x] Test
  - [x] Strategy as a separate .py file with some logic (let's say simple moving average, buy at -2% & sell at +2%
  - [ ] Pass each bar to a simple moving average that calculates last value
  - [x] Pass each bar to trading logic, which decides whether to buy or sell
  - [x] Issue an order (market order to start with, move onto limit later)
- [x] Execute
  - [x] Add a broker to match orders
    - [ ] Probably pass it with a call instead of matching later on

### Component Diagram
<p align="center">
  <img src="diagrams/minimal_trading_system.png" alt="Minimal Trading System Diagram" width="600">
  <br>
  <em>Figure 1: Minimal Backtesting & Execution System.</em>
</p>

### Alex B.'s Diagram (slightly modified)
<p align="center">
  <img src="diagrams/alex-b-s-system.png" alt="Alex B.'s System Diagram" width="200">
  <br>
  <em>Figure 2: Alex B.'s System Diagram.</em>
</p>

# Current System Overview
The main idea of the current system is to enforce a clear separation of concerns between different components of the
trading system. As for example, the Strategy component should not be concerned with parsing data or handling `DataFrame`
objects, and only needs to consider individual bars/candles of market data. Risk management converts signals into sized 
orders, and a broker (simulated or real) handles execution and fills.

## 1 System Components
- Main (e.g. `examples/example_main.py`)
  - Instantiates and links all components and begins execution.
    - Strategy class (e.g. `examples/example_strategy.py`)
      - Inherits from abstract Strategy base class (`ghosalquant` -> `Strategy` class)
        - Implements:
          - `on_start() -> None`: Called by the `ExecutionSystem` before receiving any market data.
          - `on_market_data(bar: Bar) -> Signal | None`: Called by the `ExecutionSystem` for each individual `Bar`
          received from a `DataSource`. Returns a `Signal` or `None`.
          - `on_end() -> None`: Called after all market data has been processed.
        - Provides a built-in `calculate_quantity(confidence) -> float | None` helper that linearly maps confidence to
          a quantity between `min_qty` and `max_qty` (configurable via the constructor).
    - (Optionally) Backtester (e.g. `ghosalquant.backtester.backtester_base` -> `Backtester` class)
      - Can be created directly or from a config file via the `Backtester.from_config()` class method.
      - Orchestrates the full backtesting pipeline: wires together a `DataSource`, `ExecutionSystem`, `Portfolio`,
        `RiskManager`, and optionally a `SignalSink`.
      - Implements:
        - `run() -> BacktestKPMs`: Synchronous entry point. Runs the backtest and returns key performance metrics.
        - `async run_async() -> BacktestKPMs`: Async entry point for use within an existing event loop.
      - Exposes post-run properties: `equity_curve`, `realised_pnl`, `unrealised_pnl`, `total_pnl`, `kpms`.
    - `DataFrameBacktester` (from `ghosalquant.backtester.dataframe_backtester`)
      - Subclass of `Backtester` tailored for `pandas.DataFrame` input.
      - Wraps the DataFrame in a `DataFrameDataSource` and attaches a `DataFrameSink` to record signals back onto the
        DataFrame.
      - Exposes a `dataframe` property returning the annotated DataFrame with signal/confidence/quantity columns.
    - Data source (e.g. `ghosalquant.data_layer.yfinance_datasource` -> `YFinanceDataSource`)
      - Inherits from abstract `DataSource` base class (`ghosalquant.data_layer.datasource_base`).
        - Implements:
          - `async __aiter__() -> AsyncIterable[Bar]`: Yields `Bar` instances asynchronously.
    - Execution system (`ghosalquant.execution.execution_system` -> `ExecutionSystem` class)
      - Takes `DataSource`, `Strategy`, and optionally `SignalSink`, `RiskManager`, `Portfolio`, and `Broker`.
      - Validates that a `Portfolio` is always accompanied by a `RiskManager` and a `Broker`.
      - Implements:
        - `async run() -> None`: Main loop of the execution system.
          - Connects the broker (if present).
          - Calls `strategy.on_start()`.
          - For each `Bar` from the `DataSource`:
            - Passes the bar to the `Broker` so simulation brokers can advance market state.
            - Updates portfolio equity from the bar.
            - Collects fills from the broker and passes them to the portfolio.
            - Calls `strategy.on_market_data(bar)` to get a `Signal`.
            - Forwards non-`None` signals to the `SignalSink` chain (if present).
            - Passes the signal through the `RiskManager` to produce an `Order`.
            - Submits the order to the `Broker`.
          - Calls `strategy.on_end()` and disconnects the broker on completion or interruption.
    - Signal sink (e.g. `ghosalquant.execution.sinks` -> `SignalSink` protocol)
      - Implements:
        - `async on_signal(signal: Signal) -> None`: Called by the `ExecutionSystem` to handle signals generated by
          the `Strategy`.
    - Risk manager (`ghosalquant.risk.risk_manager` -> `RiskManager`)
      - Converts a `Signal` into an `Order` (or `None` if blocked) by running a chain of `RiskRule`s against a
        mutable `OrderDraft`.
    - Portfolio (`ghosalquant.portfolio.portfolio_base` -> `Portfolio` protocol)
      - Tracks open/closed trades, equity curve, and PnL from broker fills.
    - Broker (`ghosalquant.brokerage.broker_base` -> `Broker` ABC)
      - Receives `Order`s and produces `Fill`s.

### 1.1 Data Flow
The flow of information through the current system is as follows:

```
DataSource -> MarketEvent -> ExecutionSystem -> Strategy -> Signal(s)
              (Bar |                                           |
               OrderBook |                                     +-> SignalSink chain (optional, e.g. PrintSink, DataFrameSink)
               PublicTrade)                                    |
                                                               +-> RiskManager -> Order -> Broker -> Fill -> Portfolio
```

A `DataSource` yields `MarketEvent`s: `Bar`s, `OrderBook`s, and `PublicTrade`s in any mix. For every event the
`ExecutionSystem`:
1. Passes the event to the `Broker` (`on_bar` / `on_order_book` / `on_public_trade`). A simulation broker may return
   an adjusted view of an order book for the strategy to see.
2. Marks the `Portfolio` from the **raw** event: a bar's close, a two-sided book's mid (or bid/ask under
   `MarkPolicy.CONSERVATIVE`), or a print. A one-sided book leaves the previous mark in place. Bars snapshot equity
   every time; books and prints snapshot at most once per `snapshot_interval` (and whenever a fill changed the
   accounting), so a high-frequency book feed does not bloat the equity curve.
3. Records the bar close in `price_history` (bars only — the risk rules read it as a bar series).
4. Drains any new `Fill`s from the `Broker`, feeds them into the `Portfolio`, and calls `Strategy.on_fill`.
5. Dispatches the event to `on_market_data(bar)`, `on_order_book(book, context)`, or `on_public_trade(trade, context)`
   and passes each resulting `Signal` through the sinks and the `RiskManager`; accepted orders are reported back
   through `Strategy.on_order_accepted(order, broker_order_id)`.

The `RiskContext` and `StrategyContext` for an event are built once, lazily, and shared by every signal it produces.

## 2 Core types
The primary types used throughout the system can be imported from `ghosalquant.types`.

### 2.1 Bar
```python
@dataclass(frozen=True, slots=True)
class Bar:
    time_stamp: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
```

### 2.2 Signal
Signals are emitted by the `Strategy` and are passed by the `ExecutionSystem` to a chain of `SignalSink`s and to the
`RiskManager`.
```python
class SignalType(str, Enum):
    BUY = 'BUY'
    SELL = 'SELL'

@dataclass(frozen=True, slots=True)
class Signal:
    time_stamp: datetime
    symbol: str
    signal_type: SignalType | None
    ref_price: float
    confidence: float = 1.0           # Must be between 0.0 and 1.0
    quantity: float | None = None
    metadata: dict[str, Any] | None = None
```
> **Note**: If a strategy does not wish to emit a signal for a given bar, it must return `None` rather than a `Signal`.

The `metadata` field is optionally used for additional information about a signal, such as stop loss or take profit
levels if applicable.

### 2.3 Order
Orders are the output of the `RiskManager` after evaluating a `Signal`. They represent a concrete instruction to buy
or sell a specific quantity.
```python
@dataclass(frozen=True, slots=True)
class Order:
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float          # Must be > 0
    ref_price: float
    metadata: dict[str, Any] | None = None
```

### 2.4 Fill
Fills are returned by a `Broker` to report that (part of) an order has been executed.
```python
@dataclass(slots=True)
class Fill:
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float
    price: float
    order_id: str | None = None
    broker_order_id: str | None = None
    commission: float | None = None
```

### 2.5 Trade
Trades are created and managed within a `Portfolio` implementation. A `BUY` fill opens a trade; a `SELL` fill closes
one (or more, LIFO).
```python
@dataclass(slots=True)
class Trade:
    id: int
    symbol: str
    signal_type: SignalType
    entry_price: float
    size: float
    open_time: datetime
    close_time: datetime | None = None
    realised_pnl: float | None = None
```
It has the method `unrealised_pnl(current_price: float) -> float` which calculates the unrealised profit or loss of a
trade based on a given mark price.

### 2.6 BacktestKPMs and EquityPoint
```python
@dataclass(frozen=True, slots=True)
class BacktestKPMs:
    total_trades: int
    total_wins: int
    total_losses: int
    win_rate_pct: float
    realised_pnl: float
    unrealised_pnl: float
    total_pnl: float

@dataclass(frozen=True, slots=True)
class EquityPoint:
    time_stamp: datetime
    equity: float
    realised_pnl: float
    unrealised_pnl: float
    num_open_trades: int
```

### 2.7 Risk Types
```python
@dataclass(slots=True)
class OrderDraft:
    """Mutable draft order passed across risk rules for validation and sizing."""
    time_stamp: datetime
    symbol: str
    side: SignalType
    quantity: float | None
    price: float
    metadata: dict[str, Any]

@dataclass(slots=True)
class RiskContext:
    open_positions: int = 0
    open_qty_by_symbol: dict[str, float]
    current_drawdown: float | None = None
    peak_equity: float | None = None
    price_history: dict[str, list[float]] | None = None
```

### 2.8 Order-Book Market Data
Alongside `Bar`, a data source may yield the two order-book event types. All three are `MarketEvent`s.
```python
@dataclass(frozen=True, slots=True)
class BookLevel:
    price: float
    quantity: float

@dataclass(frozen=True, slots=True)
class OrderBook:
    time_stamp: datetime
    symbol: str
    bids: tuple[BookLevel, ...]      # best first, descending price
    asks: tuple[BookLevel, ...]      # best first, ascending price
    sequence: int | None = None
    checksum: str | None = None
    # derived once on construction: best_bid, best_ask, spread, mid_price, spread_bps, bid_quantity, ask_quantity
    def is_two_sided(self) -> bool: ...
    def is_crossed(self) -> bool: ...
    def imbalance(self, depth: int = 5) -> float | None: ...
    def replace(self, **changes) -> "OrderBook": ...

@dataclass(frozen=True, slots=True)
class PublicTrade:
    time_stamp: datetime                 # exchange time
    symbol: str
    price: float
    quantity: float
    trade_id: str | None = None
    aggressor: SignalType | None = None  # side of the taker, when the feed reports it
    observed_at: datetime | None = None  # when this process saw it; None means == time_stamp

MarketEvent = Bar | OrderBook | PublicTrade

class Liquidity(str, Enum):
    MAKER = "maker"
    TAKER = "taker"
```

`OpenOrderView` is the read-only view of a resting order that brokers implementing `OpenOrderQueries` expose; it
appears in `StrategyContext.open_orders` (this symbol) and `RiskContext.open_orders` (all symbols).
`Signal`, `OrderDraft`, and `Order` carry first-class `limit_price` (``None`` means a market order) and `liquidity`
fields; `RiskManager.evaluate` copies them from the signal to the order and rules may tighten the limit on the draft.
`Order.declared_limit_price` also honours the older `metadata["limit_price"]` convention, `Order.is_limit` says whether
either is set, and `Order.effective_limit_price` falls back to `ref_price` for simulations. `Fill.liquidity` reports
maker/taker when the broker knows. `ghosalquant.core.metadata_keys` names the remaining metadata keys the framework
reads and writes (`ORDER_SLOT`, `MAX_ORDER_AGE_SECONDS`, `REASON`, ...), and `Signal.with_metadata(**updates)` returns
a copy with merged metadata.

## 3 Data Sources
Data sources are subclasses of the abstract `DataSource` base class from `ghosalquant.data_layer.datasource_base`.
The base class requires implementation of:
```python
async def __aiter__(self) -> AsyncIterable[MarketEvent]: ...
```
Sources that only know about bars simply yield bars. `merge_datasources(*sources)` (`data_layer.merge`) combines
several sources into one time-ordered stream, with a stable tiebreak at equal time: public trade, then order book,
then bar — so a print can fill a resting order before the strategy sees the post-trade book.

### 3.1 Available Data Sources
| Class | Module | Description |
|---|---|---|
| `YFinanceDataSource` | `data_layer.yfinance_datasource` | Single-symbol historical data via yfinance. Accepts start/end as datetimes or Y/M/D integers. |
| `YFinanceMultiDataSource` | `data_layer.yfinance_multi_datasource` | Multi-symbol historical data via yfinance. Used by `Backtester.from_config()`. Supports session time filtering and multi-timeframe via `_YFINANCE_TFS` mapping. |
| `DataFrameDataSource` | `data_layer.dataframe_datasource` | Wraps an existing `pandas.DataFrame` and yields `Bar` objects row by row. Supports custom column names and optional symbol/timestamp columns. |
| `ListDataSource` | `data_layer.list_datasource` | Wraps a `Sequence[MarketEvent]` (bars, order books, public trades) and yields them in order. Useful for testing. |
| `MergedDataSource` | `data_layer.merge` | K-way merge of several sources by `time_stamp`; built with `merge_datasources(...)`. |
| `ParquetPolygonDataSource` | `data_layer.parquet_polygon_datasource` | Reads Polygon OHLCV data from a local `.parquet` file. Supports date/session filtering and timeframe resampling. Driven by `Config`. |
| `LiveIBKRDataSource` | `data_layer.live_ibkr_datasource` | Streams live IBKR data via `ib_insync` and steps down to delayed data when live market data is unavailable. `events` selects `bars` (throttled ticks -> `Bar`), `order_books` (`reqMktDepth` with `depth` rows -> `OrderBook`, at most every `book_resolution_sec`), and `trades` (tick-by-tick -> `PublicTrade`). |
| `LivePolygonDataSource` | `data_layer.live_polygon_datasource` | Streams live (or 15-min delayed) data from Polygon's WebSocket API with automatic reconnection. `events` selects `bars` (`A`/`AM`, `1SEC` or `1MIN`), `quotes` (`Q` -> one-level `OrderBook`), and `trades` (`T` -> `PublicTrade`). |

The `datasource_factory.datasource_from_config(cfg)` function creates the appropriate data source from a `Config`
object (currently supports yfinance, Polygon parquet, and live IBKR). `DATA.events` (default `["bars"]`) selects the
event kinds; only `live_ibkr` can provide `order_books` and `trades`, and the factory rejects them for bar-only
sources.

## 4 Strategies
Strategies are subclasses of the abstract `Strategy` base class from `ghosalquant.execution.strategy_base`.

```python
class Strategy(ABC):
    def __init__(self, confidence_threshold: float = 0.5, max_qty: int = 10, min_qty: int = 0): ...

    @abstractmethod
    def on_start(self) -> None: ...

    @abstractmethod
    def on_market_data(self, bar: Bar) -> Signal | None: ...

    @abstractmethod
    def on_end(self) -> None: ...

    # Optional hooks with no-op defaults
    def on_order_book(self, book: OrderBook, context: StrategyContext) -> SignalResult: ...
    def on_public_trade(self, trade: PublicTrade, context: StrategyContext) -> SignalResult: ...
    def on_fill(self, fill: Fill) -> None: ...
    def on_order_accepted(self, order: Order, broker_order_id: str | None) -> None: ...

    def calculate_quantity(self, confidence) -> float | None: ...
```

- `on_start()`: Called before any market data is received.
- `on_market_data(bar)`: Called for each `Bar`. Should return a `Signal` or `None`.
- `on_order_book(book, context)`: Called for each `OrderBook`. May return `None`, one `Signal`, or an iterable of
  signals (`SignalResult`). Default: no-op.
- `on_public_trade(trade, context)`: Called for each `PublicTrade`. Same contract. Default: no-op.
- `on_fill(fill)`: Called after the `Portfolio` processed a fill. Default: no-op.
- `on_order_accepted(order, broker_order_id)`: Called after the `Broker` accepted an order. Default: no-op.
- `on_end()`: Called after all market data has been processed.
- `calculate_quantity(confidence)`: Built-in helper that returns `max_qty` if confidence >= threshold, else `min_qty`.
  Strategies may override this.

`StrategyContext` (`ghosalquant.execution.context`) is what the two order-book hooks receive alongside the event:
```python
@dataclass(frozen=True, slots=True)
class StrategyContext:
    symbol: str
    position_quantity: float
    average_entry_price: float | None
    cash: float
    open_orders: tuple[OpenOrderView, ...]   # this symbol's resting orders, when the broker reports them
    risk: RiskContext
    # helpers: has_open_orders, open_buy_quantity, open_sell_quantity, open_orders_for(side), open_quantity_at(price, side)
```

`OrderBookStrategy` is a `Strategy` for which `on_order_book` is mandatory and `on_market_data` is a no-op — the
base class for strategies driven by books and prints rather than bars.

### 4.1 Included Benchmark Strategies
| Class | Module | Description |
|---|---|---|
| `BenchmarkBuyAndHold` | `execution.benchmark_buy_and_hold` | Sends a single `BUY` signal per symbol on first bar, then holds. Optionally includes a catastrophic stop loss. |
| `BenchmarkTrendVolTarget` | `execution.benchmark_trend_vol_target` | EMA crossover trend-following strategy with volatility targeting and position sizing. Uses EMA-20/80/200 and exponentially weighted volatility. |

## 5 Signal Sinks
Signal sinks implement the `SignalSink` protocol from `ghosalquant.execution.sinks`.

```python
class SignalSink(Protocol):
    async def on_signal(self, signal: Signal) -> None: ...
```

### 5.1 Available Sinks
| Class | Description |
|---|---|
| `PrintSink` | Prints the signal to the console, then forwards to next sink. |
| `DataFrameSink` | Records signal type, confidence, quantity, and metadata fields back into a DataFrame row matched by timestamp. |
| `ListSink` | Appends each signal to an internal list (retrievable via `get_signals()`). |

### 5.2 Chaining Signal Sinks
Signal sinks can be chained by passing a `next_sink` argument to the constructor:
```python
PrintSink(
    next_sink=ListSink()
)
```

## 6 Risk Management
Risk management sits between signal generation and order execution. The `RiskManager` (from
`ghosalquant.risk.risk_manager`) converts a `Signal` into an `Order` by running a pipeline of `RiskRule`s.

### 6.1 How it works
1. A `Signal` is converted into a mutable `OrderDraft`.
2. Each `RiskRule` in the chain calls `apply(draft, context) -> bool`.
   - Rules can mutate the draft (e.g. resize quantity) or return `False` to block the order entirely.
3. If all rules pass and the draft has a positive quantity, an immutable `Order` is created and returned.

### 6.2 Available Risk Rules
| Rule | Description |
|---|---|
| `MaxOpenPositions` | Blocks `BUY` orders when the number of open positions reaches a configured limit (default 3). |
| `ConfidenceSizer` | Linearly interpolates quantity between `min_quantity` and `max_quantity` based on signal confidence. Blocks the order if the resulting quantity is zero. |
| `RegimeFilter` | Blocks or penalises orders based on `draft.metadata["regime"]`. |
| `DrawdownKillSwitch` | Reduces or blocks new `BUY` orders based on portfolio drawdown. |
| `CorrelationFilter` | Blocks new `BUY` orders when the proposed symbol is too correlated with currently held symbols. |

### 6.3 Building from Config
`build_risk_manager(risk_config)` in `ghosalquant.risk.builder` constructs a `RiskManager` from a `RiskConfig`
(parsed from the `RISK` section of `config.yaml`).

## 7 Backtesting
Backtesting is handled by the `Backtester` base class from `ghosalquant.backtester.backtester_base`.

### 7.1 `Backtester`
- **Constructor**: Accepts `strategy`, `data_src`, and optionally `signal_destination`, `portfolio`, `risk_manager`,
  and `config`. If no portfolio is given, a `BasicPortfolio` with 10,000 initial capital is created. If
  `config.system.use_risk_manager` is set, a risk manager is built automatically from config.
- **`Backtester.from_config(strategy, config_path, ...)`**: Class method that loads a `Config`, creates the
  appropriate `DataSource` via `datasource_from_config`, and wires everything together.
- **`run() -> BacktestKPMs`**: Runs the backtest synchronously.
- **`run_async() -> BacktestKPMs`**: Runs the backtest within an existing async event loop.
- Post-run properties: `equity_curve`, `realised_pnl`, `unrealised_pnl`, `total_pnl`, `kpms`.

### 7.2 `DataFrameBacktester`
A convenience subclass of `Backtester` for when you already have market data in a `pandas.DataFrame`. It:
- Creates a `DataFrameDataSource` from the DataFrame.
- Attaches a `DataFrameSink` to annotate the DataFrame with signal/confidence/quantity columns.
- Loads config from a YAML path for symbol info and risk rules.
- Handles `MultiIndex` column flattening (e.g. from multi-ticker yfinance downloads).

## 8 Portfolio Management
Portfolio management is defined by the `Portfolio` protocol from `ghosalquant.portfolio.portfolio_base`.

```python
class Portfolio(Protocol):
    async def process_fill(self, fill: Fill) -> None: ...
    def mark(self, symbol: str, price: float, time_stamp: datetime) -> None: ...   # records a price, no snapshot
    def update_equity(self, bar: Bar) -> None: ...                                  # mark + snapshot
    def snapshot_equity(self, time_stamp: datetime, prices: dict[str, float]) -> None: ...
    def average_entry_price(self, symbol: str) -> float | None: ...

    @property
    def closed_trades(self) -> Iterable[Trade]: ...
    @property
    def equity_curve(self) -> Iterable[EquityPoint]: ...
    @property
    def realised_pnl(self) -> float: ...
    @property
    def unrealised_pnl(self) -> float: ...
    @property
    def total_pnl(self) -> float: ...
    @property
    def final_balance(self) -> float: ...
```

### 8.1 `BasicPortfolio`
The default implementation (`ghosalquant.portfolio.basic_portfolio`). Key behaviours:
- **`process_fill(fill)`**: Opens a trade on `BUY` fills (deducting cash for notional + commission) and closes trades
  on `SELL` fills using LIFO order. Supports partial closes.
- **`mark(symbol, price, time_stamp)`**: Updates the last known price for a symbol without recording an
  `EquityPoint`; the `ExecutionSystem` decides when a snapshot is due.
- **`update_equity(bar)`**: `mark(...)` followed by a snapshot (an `EquityPoint` per bar).
- **`average_entry_price(symbol)`**: Open cost divided by open quantity, or `None` when flat.
- Tracks `fees_paid` separately.

`ghosalquant.portfolio.marks` holds the `MarkPolicy` (`MID` or `CONSERVATIVE`) and the `mark_from_book` /
`mark_from_trade` functions the loop uses.

## 9 Brokers
Brokers are subclasses of the abstract `Broker` base class from `ghosalquant.brokerage.broker_base`.

```python
class Broker(ABC):
    async def connect(self) -> None: ...      # Optional, no-op by default
    async def disconnect(self) -> None: ...   # Optional, no-op by default
    def on_bar(self, bar: Bar) -> None: ...   # Optional, used by simulation brokers
    def on_order_book(self, book: OrderBook) -> OrderBook: ...   # Optional; returns the book the strategy sees
    def on_public_trade(self, trade: PublicTrade) -> None: ...  # Optional
    def on_market_event(self, event: MarketEvent) -> MarketEvent: ...  # Routes to the three hooks above

    @abstractmethod
    def submit(self, order: Order) -> str | None: ...

    @abstractmethod
    def get_fills(self) -> list[Fill]: ...
```

A broker may additionally implement the `OpenOrderQueries` protocol (`ghosalquant.brokerage.open_orders`) —
`open_order_views(symbol=None)` and `cancel_open_orders(symbol, side=None)` — in which case the `ExecutionSystem`
fills `StrategyContext.open_orders` and `RiskContext.open_orders` from it. `SimBroker` does.

### 9.1 `SimBroker`
A simulated broker (`ghosalquant.brokerage.simulated`) with configurable:
- Spread (basis points)
- Slippage (mean and std, in basis points)
- Commission per share
- Partial fill probability
- Reject probability
- Maximum participation per bar volume
- Participation-based market impact
- Latency range (milliseconds)
- Deterministic seed for reproducibility

Orders are submitted after a strategy sees a bar and become eligible on a later bar once latency has elapsed. The
broker uses `on_bar(bar)` to advance simulated market time, caps fills by bar volume participation, and prices fills
from the eligible bar with spread, slippage, and participation-based impact.

Against an `OrderBook` the broker applies a deliberately naive touch-crossing rule: an eligible order crosses the
touch (a `BUY` at the best ask, a `SELL` at the best bid) for up to the displayed quantity at that level, and an
order whose `limit_price` the touch has not reached rests. There is no queue position, participation cap, or depth
sweeping. Public trades only advance market time.

### 9.2 `IBKRBroker`
A live broker implementation (`ghosalquant.brokerage.ibkr`) using `ib_insync` to connect to Interactive Brokers TWS
or Gateway. Supports:
- Async connect/disconnect.
- Submitting market orders via the IBKR API.
- Receiving fills through the `execDetailsEvent` callback.

### 9.3 Broker Builder
`build_broker(broker_config)` in `ghosalquant.brokerage.builder` constructs a `SimBroker` or `IBKRBroker` from a
`BrokerConfig` dataclass.

## 10 Configuration
The system can be configured with a `Config` dataclass, created from a `config.yaml` file and parsed by
`ghosalquant.util.config.load_config`.

### 10.1 Config Structure
The YAML is split into the following sections:

- **`SYSTEM`**: Execution parameters.
  - `symbols`: List of ticker symbols.
  - `main_timeframe`: Timeframe for market data (e.g. `1D`, `1H`, `5Min`).
  - `execution_timeframe`: Timeframe for signal evaluation.
  - `fresh_candles`: Whether to re-download/update data before running.
  - `use_risk_manager`: Whether to enable the built-in risk manager.
- **`DATA`**: Data source selection and settings.
  - `source`: `"yfinance"`, `"polygon"`, or `"live_ibkr"`.
  - `polygon`: Polygon-specific settings (`api_key`, `parquet_path`, `base_resolution`).
  - `live_ibkr`: IBKR market-data settings (`host`, `port`, `client_id`, `bar_resolution_sec`).
  - `yfinance`: Currently empty, reserved for future options.
- **`TIME_RANGE`**: Bounds for backtesting.
  - `start_date`, `end_date`: Date range.
  - `session_start_time`, `session_end_time`: Intraday session window.
- **`RISK`**: Risk rules applied when `use_risk_manager` is `True`.
  - `rules`: A list of rule definitions, each with a `name` and rule-specific parameters.
- **`BROKERAGE`** (optional): Broker configuration. If omitted, `load_config()` defaults to a simulated broker with default parameters. `BROKERAGE.type` selects the active broker, and inactive broker-specific sections are ignored.

### 10.2 Allowed Timeframes
`1Min`, `5Min`, `15Min`, `30Min`, `1H`, `2H`, `4H`, `1D`, `1W`, `1M`

### 10.3 Example `config.yaml`
```yaml
SYSTEM:
  symbols: ["NVDA", "SPY"]
  main_timeframe: "1D"
  execution_timeframe: "5Min"
  fresh_candles: False
  use_risk_manager: True

DATA:
  source: "yfinance"
  yfinance: {}
  # live_ibkr:
  #   host: "127.0.0.1"
  #   port: 7497
  #   client_id: 2
  #   bar_resolution_sec: 1

TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2025-12-31"
  session_start_time: "09:00"
  session_end_time: "17:00"

RISK:
  rules:
    - name: "MaxOpenPositions"
      max_open_positions: 3
    - name: "ConfidenceSizer"
      min_quantity: 0.0
      max_quantity: 10.0

BROKERAGE:
  type: "simulated"
  mode: "paper"
  sim:
    spread_bps: 2.0
    slippage_mean_bps: 0.5
    slippage_std_bps: 1.0
  # ibkr:
  #   host: "127.0.0.1"
  #   port: 4002
  #   client_id: 1
```

## 11 Visualisation
`ghosalquant.util.visualisation` provides `plot_equity_curve()` for plotting equity curves using `matplotlib`.

```python
def plot_equity_curve(
    equity_curve: Iterable[EquityPoint],
    *,
    title: str = "Equity Curve",
    label: str = "Equity",
    show_realised_unrealised: bool = False,
    ax: plt.Axes | None = None,
) -> plt.Axes:
```
- Pass an existing `ax` to overlay multiple equity curves on the same chart (useful for strategy comparison).
- Set `show_realised_unrealised=True` to generate a second figure showing realised vs unrealised PnL over time.

## 12 Backtest UI (Prototype)
A prototype GUI (`src/backtest_ui/`) built with `customtkinter` that lets users select a config file and strategy
file, then runs a `DataFrameBacktester` in a subprocess and displays logs and plots. This is experimental and not
part of the core library.

