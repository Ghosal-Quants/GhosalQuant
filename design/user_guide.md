# Guide to using the GhosalQuant library (for QRs)
This guide provides a high level overview of how to get started using the GhosalQuant library for developing strategies 
and backtesting.

## Installation
To install the GhosalQuant library from PyPI:
```bash
pip install ghosalquant
```
To work on the library itself, install it from the root directory of a clone instead:
```bash
pip install -e .
```
This installs the library in editable mode, so your changes to the code take effect immediately.

## Creating a Strategy
The way strategies are created in GhosalQuant is by creating a class which inherits from the `Strategy` base class as 
follows (hopefully your strategies will be more intelligent than this example!):

```python
from ghosalquant import Bar, Signal, SignalType, Strategy


class MyStrategy(Strategy):
    def __init__(self):
        super().__init__()
        self.prev_bar: Bar | None = None

    def on_start(self):  # Required, but can be left empty
        print("MyStrategy started executing.")

    def on_end(self):  # Required, but can be left empty
        print("MyStrategy finished executing.")
        # You can use this to perform any cleanup or final calculations at the end of a backtest or trading session

    def on_market_data(self, bar: Bar) -> Signal | None:
        if self.prev_bar is None:
            self.prev_bar = bar
            return None

        signal_type: SignalType

        if bar.close > self.prev_bar.close:
            signal_type = SignalType.SELL
        elif bar.close < self.prev_bar.close:
            signal_type = SignalType.BUY
        else:
            self.prev_bar = bar
            return None

        self.prev_bar = bar
        confidence = 1.0  # Actual strategies should employ more robust confidence calculations
        # A confidence of 1.0 will mean the risk engine will be unlikely to reject or downscale the signal
        # A confidence of 0.0 will mean the risk engine will be likely to reject or downscale the signal

        return Signal(
            time_stamp=bar.time_stamp,
            symbol=bar.symbol,
            signal_type=signal_type,
            ref_price=bar.close,
            confidence=confidence,
        )
```

### Sizing a signal
Each signal is sized by one of:
- `quantity=`: units to trade.
- `metadata={metadata_keys.TARGET_WEIGHT: w}` (`from ghosalquant import metadata_keys`): the position to hold after
  the trade, as a share of equity; `0.0` closes it. The risk manager nets it against what is held and resting, sets the
  side from the difference, and caps a buy at the cash not committed to resting buys, less `RISK.cash_buffer`
  (default 1%) of equity.
- a sizing rule in `RISK`, such as `ConfidenceSizer` or `SymbolCapitalFractionSizer`.

A signal none of these sizes is dropped with a `WARNING` log, once per symbol.

## Config File
A config is optional. Without one you get a simulated broker and no risk rules, which is enough for a
`DataFrameBacktester`. Settings come from a `Config` object (`ghosalquant.util.config`) or from a YAML file read with
`Config.from_yaml`; see
[`examples/config.yaml`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/config.yaml) for a full
example. Every section is optional except `SYSTEM`, and `DATA` and `TIME_RANGE` are only needed when the library loads
the bars itself (`Backtester.from_config`). The sections:

```yaml
SYSTEM:
  symbols: ["AAPL"]
  main_timeframe: "1D"
  execution_timeframe: "5Min"
  fresh_candles: False
  use_risk_manager: True   # the default; False ignores the RISK rules

DATA:
  source: "yfinance"   # or "polygon" / "live_ibkr" / "live_alpaca"
  events: ["bars"]     # the live sources can also stream "order_books" and "trades"
  yfinance: {}
  # live_ibkr:
  #   host: "127.0.0.1"
  #   port: 7497
  #   client_id: 2
  #   bar_resolution_sec: 1
  #   depth: 5                 # market-depth rows per side for "order_books"
  #   book_resolution_sec: 0   # 0 = emit every depth update

TIME_RANGE:
  start_date: "2024-01-01"
  end_date: "2025-12-31"
  session_start_time: "09:00"
  session_end_time: "17:00"

RISK:
  cash_buffer: 0.01    # share of equity a TARGET_WEIGHT buy keeps in cash for the move before it fills
  rules:
    - name: "MaxOpenPositions"
      max_open_positions: 3
    - name: "ConfidenceSizer"
      min_quantity: 0.0
      max_quantity: 10.0

BROKERAGE:
  type: "simulated"
  mode: "paper"
  sim: {}
  # ibkr:
  #   host: "127.0.0.1"
  #   port: 4002
  #   client_id: 1
```

`BROKERAGE.type` selects the active broker configuration. It is fine to keep both `sim` and `ibkr` sections in the
same file and only use one at a time.

`DATA.source` works the same way for market data. A live IBKR configuration stays under `DATA.live_ibkr`, separate
from `BROKERAGE.ibkr`, so you can switch data source and execution broker independently.

## Running a Backtest

### Option 1: DataFrameBacktester (bring your own DataFrame)
```python
import yfinance as yf
from matplotlib import pyplot as plt
from my_strategy import MyStrategy
from ghosalquant.backtester.dataframe_backtester import DataFrameBacktester
from ghosalquant.util.visualisation import plot_equity_curve

df = yf.download("AAPL", start="2024-01-01", end="2025-12-31", auto_adjust=True, progress=False)

backtester = DataFrameBacktester(
    strategy=MyStrategy(),
    dataframe=df,
    config_path="config.yaml",  # or config=Config(...), or leave both out for the defaults
    symbol="AAPL",
)

kpms = backtester.run()
print(kpms)

plot_equity_curve(backtester.equity_curve, show_realised_unrealised=True)
plt.show()
```
The bars are traded as `symbol`, or as the first of `SYSTEM.symbols` when it is left out. The DataFrame supplies the
dates, so `DATA` and `TIME_RANGE` are not read.

`MyStrategy` above sends no quantity, so it relies on the `ConfidenceSizer` rule in `RISK` to size its signals (see
[Sizing a signal](#sizing-a-signal)).

### Option 2: Backtester.from_config (data sourced from config)
```python
from my_strategy import MyStrategy
from ghosalquant.backtester.backtester_base import Backtester

backtester = Backtester.from_config(
    strategy=MyStrategy(),
    config_path="config.yaml"
)
kpms = backtester.run()
print(kpms)
```
This creates the data source from the config's `DATA` and `TIME_RANGE`, which it needs. It also takes `config=`.

### What you get back
`backtester.run()` returns a `BacktestKPMs` object with: `total_trades`, `total_wins`, `total_losses`, `win_rate_pct`,
`realised_pnl`, `unrealised_pnl`, `total_pnl`, `open_positions`, `fills`, plus the risk-adjusted set — `max_drawdown`,
`annualized_return_pct`, `return_volatility_pct`, `downside_volatility_pct`, `sharpe_ratio`, `sortino_ratio`,
`calmar_ratio`. Ratios the equity path cannot support (a flat curve, too few points, no losing period) are `None`.

`total_trades` counts closed lots: one per lot, or part of a lot, that an opposite fill closed. A position held to the
end adds nothing to it, and one exit can close several lots. `open_positions` is the number of symbols still held at
the end, and `fills` the number of fills the portfolio booked.

To score a curve directly, or to gate a change before promoting it:

```python
from ghosalquant.metrics import GateSpec, MetricRef, evaluate_gates, risk_adjusted_metrics

metrics = risk_adjusted_metrics(backtester.equity_curve, init_capital=10_000.0)

evaluation = evaluate_gates(
    {**metrics, "benchmark_sharpe": 0.8, "closed_trades": kpms.total_trades},
    [
        GateSpec("sharpe", MetricRef("sharpe_ratio"), ">", MetricRef("benchmark_sharpe")),
        GateSpec("enough_trades", MetricRef("closed_trades"), ">=", 30.0, message="too few trades to trust"),
    ],
)
print(evaluation)
```

You can also access `backtester.equity_curve`, `backtester.realised_pnl`, `backtester.unrealised_pnl`, etc. after the 
run.

## Comparing Strategies
You can overlay multiple equity curves on one chart:
```python
from ghosalquant.util.visualisation import plot_equity_curve

ax = plot_equity_curve(bt1.equity_curve, label="My Strategy")
plot_equity_curve(bt2.equity_curve, label="Buy & Hold", ax=ax)
plt.show()
```

## Running Live / Paper (without backtesting)
You can use the `ExecutionSystem` directly with a real or simulated broker:
```python
import asyncio
from my_strategy import MyStrategy
from ghosalquant.execution.execution_system import ExecutionSystem
from ghosalquant.data_layer.yfinance_datasource import YFinanceDataSource
from ghosalquant.risk.risk_manager import RiskManager
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.brokerage.ibkr import IBKRBroker  # or SimBroker for simulated

execution_system = ExecutionSystem(
    strategy=MyStrategy(),
    data_src=YFinanceDataSource("AAPL", start="2025-01-01", end="2025-12-31"),
    risk_manager=RiskManager(),
    portfolio=BasicPortfolio(),
    broker=IBKRBroker(port=4002),  # connects to TWS/Gateway
)

asyncio.run(execution_system.run())
```

## Writing an Order-Book Strategy
A data source may yield `OrderBook` and `PublicTrade` events as well as `Bar`s, and the same `ExecutionSystem` routes
each to the matching hook. Subclass `OrderBookStrategy` (bars are ignored, `on_order_book` is mandatory) and use the
`StrategyContext` you are handed to see your own position and resting orders for that symbol:

```python
from ghosalquant import Fill, Liquidity, OrderBook, OrderBookStrategy, Signal, SignalType
from ghosalquant.execution.context import StrategyContext


class JoinTheBid(OrderBookStrategy):
    def on_start(self): ...
    def on_end(self): ...

    def on_order_book(self, book: OrderBook, context: StrategyContext):
        if not book.is_two_sided():
            return None
        if context.position_quantity == 0.0 and not context.has_open_orders:
            price = book.best_bid.price                       # rest at the bid (a maker order)
            return Signal(
                time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.BUY,
                ref_price=price, quantity=10.0,
                limit_price=price, liquidity=Liquidity.MAKER, # a limit order; omit limit_price for a market order
            )
        if context.position_quantity > 0.0 and context.open_sell_quantity == 0.0:
            price = round(context.average_entry_price + 0.02, 2)
            return [Signal(                                   # a hook may return several signals
                time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.SELL,
                ref_price=price, quantity=context.position_quantity,
                limit_price=price, liquidity=Liquidity.MAKER,
            )]
        return None

    def on_fill(self, fill: Fill) -> None:                    # optional lifecycle hooks
        print("filled", fill)
```

Feed it books (and prints, if you have them) through any `DataSource`; `merge_datasources` combines several
streams into one time-ordered stream:

```python
from datetime import timedelta
from ghosalquant.data_layer import ListDataSource, merge_datasources
from ghosalquant.portfolio import BasicPortfolio, MarkPolicy

execution_system = ExecutionSystem(
    strategy=JoinTheBid(),
    data_src=merge_datasources(ListDataSource(books), ListDataSource(prints)),
    risk_manager=RiskManager(),
    portfolio=BasicPortfolio(),
    broker=SimBroker(),
    mark_policy=MarkPolicy.MID,               # or CONSERVATIVE: bid for longs, prints never raise a mark
    snapshot_interval=timedelta(seconds=1),   # equity points at most this often between bars and fills
)
asyncio.run(execution_system.run())
```

`Signal.limit_price` and `Signal.liquidity` travel through the `RiskManager` (rules may tighten the limit) onto the
`Order`; `IBKRBroker` then sends a `LimitOrder` instead of a `MarketOrder`.

Use `OrderBookSimBroker` for order-book work:

```python
from ghosalquant.brokerage import OrderBookSimBroker

broker = OrderBookSimBroker(
    maker_rebate_bps=2.0,          # credited as a negative Fill.commission
    taker_fee_bps=5.0,             # both are required
    public_trade_participation=0.5,  # at most half of each print can reach your orders
    lot_size=1.0,                  # optional: floor fills to whole units
    max_order_age_seconds=300.0,   # optional TTL
)
```

A resting maker order queues behind the size already displayed at its price and fills only when public prints trade
through it, so joining the bid earns the spread rather than paying it. `SimBroker` fills against bars only, so point
it at bar data and `OrderBookSimBroker` at level-2 data. Both are presets over one `SimulatedBroker` that owns the
order lifecycle, so latency, rejection and TTL expiry work the same either way. A full example with synthetic books
and prints is in `examples/example_order_book_strategy.py`.

## Recording a Session and Replaying It

Wrap a live source to keep a copy of everything it produced. It yields exactly what it records, so the live run
is unaffected.

```python
from ghosalquant.data_layer import RecordedDataSource, RecordingDataSource

source = RecordingDataSource(LiveIBKRDataSource(...), "recordings/2026-03-02")
await ExecutionSystem(source, strategy, portfolio=portfolio, risk_manager=risk, broker=broker).run()
```

The recording is a directory of durable streams: books are kept only when the visible book changes, prints are
deduplicated by id, and each stream is a write-ahead log that seals into verified Parquet segments as it fills.
Replay it as a data source:

```python
replay = RecordedDataSource("recordings/2026-03-02", symbols=["AAPL"])
await ExecutionSystem(replay, strategy, portfolio=portfolio, risk_manager=risk, broker=OrderBookSimBroker(...)).run()
```

`kinds`, `symbols`, `start` and `end` narrow a replay. The repeated books that were compressed away are replayed
too, so a strategy is woken as often as it was live; pass `rehydrate_unchanged=False` if you only want the changes.
To read a recording outside the framework, `iter_stream_records(path)` yields every record in order, sealed or not.

## Quick Reference

| What you want | What to use |
|---|---|
| Trade order books / prints | `OrderBookStrategy` + `on_order_book(book, context)` |
| Simulate resting limit orders | `OrderBookSimBroker` |
| Sharpe / Sortino / Calmar / drawdown | `risk_adjusted_metrics(...)` (also on `BacktestKPMs`) |
| Value a portfolio at what you could sell | `PortfolioValuationEngine` |
| Go/no-go check before promoting | `evaluate_gates(metrics, spec)` |
| Size by money, not share count | `SymbolCapitalFractionSizer` |
| Cap one symbol's exposure | `MaxSymbolExposure` |
| Keep cash free for a better order | `CashAllocationRule` |
| Combine several data streams | `merge_datasources(...)` |
| Record a live session to disk | `RecordingDataSource(source, dir)` |
| Replay a recorded session | `RecordedDataSource(dir)` |
| Backtest with a DataFrame | `DataFrameBacktester` (config optional) |
| Backtest from config (auto data) | `Backtester.from_config()` |
| Configure in Python | `Config(...)`, or `Config.from_yaml(path)` |
| Size as a share of equity | `metadata={metadata_keys.TARGET_WEIGHT: w}` on the `Signal` |
| Live / paper trading | `ExecutionSystem` + `IBKRBroker` |
| Simulated execution | `ExecutionSystem` + `SimBroker` |
| Plot equity curves | `plot_equity_curve()` |
| Compare strategies | Pass `ax=` to overlay on same chart |

