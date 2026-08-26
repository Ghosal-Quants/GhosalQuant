# Guide to using the GhosalQuant library (for QRs)
This guide provides a high level overview of how to get started using the GhosalQuant library for developing strategies 
and backtesting.

## Installation
To install the GhosalQuant library, you can use pip:
```bash
pip install -e .
```
from the root directory of the project. This will install the library in editable mode, allowing you to make changes to 
the code and have them reflected immediately, and keep up to date with the remote repository.

## Creating a Strategy
The way strategies are created in GhosalQuant is by creating a class which inherits from the `Strategy` base class as 
follows (hopefully your strategies will be more intelligent than this example!):

```python
from ghosalquant import Strategy
from ghosalquant.core.types import Bar, Signal, SignalType


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

## Config File
Before running a backtest, create a `config.yaml` (see `examples/config.yaml` for a full example). The key sections:

```yaml
SYSTEM:
  symbols: ["AAPL"]
  main_timeframe: "1D"
  execution_timeframe: "5Min"
  fresh_candles: False
  use_risk_manager: True

DATA:
  source: "yfinance"   # or "polygon" / "live_ibkr"
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
    config_path="config.yaml"
)

kpms = backtester.run()
print(kpms)

plot_equity_curve(backtester.equity_curve, show_realised_unrealised=True)
plt.show()
```

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
This creates the data source automatically based on whatever is in your config file.

### What you get back
`backtester.run()` returns a `BacktestKPMs` object with: `total_trades`, `total_wins`, `total_losses`, `win_rate_pct`, 
`realised_pnl`, `unrealised_pnl`, `total_pnl`.

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
from ghosalquant import OrderBookStrategy
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import Fill, OrderBook, Signal, SignalType
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
                metadata={metadata_keys.LIMIT_PRICE: price},  # the price the broker should use
            )
        if context.position_quantity > 0.0 and context.open_sell_quantity == 0.0:
            price = round(context.average_entry_price + 0.02, 2)
            return [Signal(                                   # a hook may return several signals
                time_stamp=book.time_stamp, symbol=book.symbol, signal_type=SignalType.SELL,
                ref_price=price, quantity=context.position_quantity,
                metadata={metadata_keys.LIMIT_PRICE: price},
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

`SimBroker` fills an order against a book when the touch reaches its price, for up to the displayed quantity at
that level. A full example with synthetic books is in `examples/example_order_book_strategy.py`.

## Quick Reference

| What you want | What to use |
|---|---|
| Trade order books / prints | `OrderBookStrategy` + `on_order_book(book, context)` |
| Combine several data streams | `merge_datasources(...)` |
| Backtest with a DataFrame | `DataFrameBacktester` |
| Backtest from config (auto data) | `Backtester.from_config()` |
| Live / paper trading | `ExecutionSystem` + `IBKRBroker` |
| Simulated execution | `ExecutionSystem` + `SimBroker` |
| Plot equity curves | `plot_equity_curve()` |
| Compare strategies | Pass `ax=` to overlay on same chart |

