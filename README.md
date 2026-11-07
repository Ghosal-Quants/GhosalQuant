# GhosalQuant
Ghosal Fund | Quantitative Trading Team Repository

An event-driven Python framework for backtesting and trading quantitative strategies. A strategy turns market events
(bars, order books, public trades) into signals. The same strategy class runs on historical data, recorded sessions,
a simulated broker or Interactive Brokers.

## Documentation
- [User Guide](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/design/user_guide.md) - writing strategies,
  configuring and running backtests, live trading, recording
- [System Overview](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/design/system-overview.md) - component
  reference: core types, data sources, risk rules, brokers, configuration

## Installation
Requires Python 3.10 or later.

### Using a release
To write and backtest strategies, install the published package from [PyPI](https://pypi.org/project/ghosalquant/):
```bash
pip install ghosalquant
```
Pin a version (`pip install ghosalquant==0.1.0`) to keep results reproducible, and `pip install -U ghosalquant` to
upgrade. Optional extras add integrations:

| Extra | Adds |
|---|---|
| `alpaca` | Alpaca market data (`alpaca-py`) |
| `plotting` | Equity curve and results charts (`matplotlib`, `plotly`) |
| `ui` | The prototype backtest GUI, launched with `ghosalquant-ui` |

```bash
pip install "ghosalquant[plotting,ui]"
```

API keys for Alpaca and Polygon are read from environment variables or a `.env` file beside your config (see
[`.env.example`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/.env.example)). They are refused if written
into the config file.

### For developers
To work on the framework itself, clone the repository and install it in editable mode, so your changes take effect
without reinstalling:
```bash
git clone https://github.com/Ghosal-Quants/GhosalQuant.git
cd GhosalQuant
pip install -e ".[plotting,ui]" pytest
python -m pytest tests --ignore=tests/legacy
```
Copy `.env.example` to `.env` and fill in any API keys you need.

Releases are published by CI: bump `__version__` in `src/ghosalquant/__init__.py`, push to `main`, then publish a
GitHub release tagged with that version (e.g. `v0.1.1`). The workflow runs the tests, builds the package and uploads
it to PyPI, and fails if the tag and version don't match.

## Quick Start
Subclass `Strategy`, return a `Signal` from `on_market_data`, and backtest it on any OHLCV DataFrame. This runs as is,
with no config file, network or API keys:
```python
import numpy as np
import pandas as pd

from ghosalquant import Signal, SignalType, Strategy, metadata_keys
from ghosalquant.backtester import DataFrameBacktester


class Trend(Strategy):
    """Fully invested above the 50-day average, flat below it."""

    def on_start(self):
        self.closes = []

    def on_end(self):
        pass

    def on_market_data(self, bar):
        self.closes.append(bar.close)
        if len(self.closes) < 50:
            return None
        weight = 1.0 if bar.close > np.mean(self.closes[-50:]) else 0.0
        return Signal(
            time_stamp=bar.time_stamp, symbol=bar.symbol, ref_price=bar.close,
            signal_type=SignalType.BUY if weight else SignalType.SELL,
            metadata={metadata_keys.TARGET_WEIGHT: weight},
        )


# Two years of synthetic daily bars; any DataFrame with OHLCV columns works.
rng = np.random.default_rng(0)
close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, 500)))
open_ = np.r_[100.0, close[:-1]] * np.exp(rng.normal(0.0, 0.003, 500))
df = pd.DataFrame(
    {"Open": open_, "High": np.maximum(open_, close), "Low": np.minimum(open_, close),
     "Close": close, "Volume": 1e6},
    index=pd.bdate_range("2023-01-02", periods=500),
)

kpms = DataFrameBacktester(strategy=Trend(), dataframe=df, symbol="DEMO").run()
print(kpms)
```
`run()` returns a `BacktestKPMs`: trade counts and win rate, realised and unrealised P&L, open positions, fills, max
drawdown, annualised return and volatility, and the Sharpe, Sortino and Calmar ratios. `total_trades` counts closed
lots: one per lot, or part of a lot, that an opposite fill closed. The backtester also exposes `equity_curve`, and
`plot_equity_curve` (`ghosalquant.util.visualisation`, `plotting` extra) draws it.

Every signal needs a size: a `quantity`, a `TARGET_WEIGHT` (a share of equity) in its metadata, or a sizing rule
such as `ConfidenceSizer` in the risk rules. A signal with none of these is dropped, with a warning. A
`TARGET_WEIGHT` buy is capped at the cash not committed to resting buys, less `RISK.cash_buffer` (default 1%) of
equity.

A simulated market order fills at the next bar's open, with the broker's spread, slippage and impact applied.

### Configuring a run
With no config you get a simulated broker and no risk rules. To change that, pass a `Config`:
```python
from ghosalquant.util.config import BrokerConfig, Config, RiskConfig, RiskRuleConfig, SimBrokerConfig

config = Config(
    broker=BrokerConfig(sim=SimBrokerConfig(spread_bps=5.0, commission_per_share=0.005)),
    risk=RiskConfig(rules=[RiskRuleConfig("MaxOpenPositions", {"max_open_positions": 3})]),
)
kpms = DataFrameBacktester(strategy=Trend(), dataframe=df, config=config).run()
```
Or keep the same settings in YAML and pass `config_path="config.yaml"`, or load it with `Config.from_yaml`; see
[`examples/config.yaml`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/config.yaml). Only `SYSTEM`
is required. To have the library load the bars too, set `DATA` (`yfinance`, `polygon`, `live_ibkr` or
`live_alpaca`), plus `TIME_RANGE` for the historical sources, and use
`Backtester.from_config(strategy=Trend(), config_path="config.yaml")`.

## Components

### Strategies and execution
- `Strategy` handles bars (`on_market_data`). `OrderBookStrategy` handles order books and prints (`on_order_book`,
  `on_public_trade`), with a `StrategyContext` holding the symbol's position, cash and resting orders. Optional
  hooks: `on_fill`, `on_order_accepted`, `on_order_expired`. A hook returns `None`, one `Signal` or several.
- A `Signal` becomes a market order, or a limit order when it sets `limit_price`, with `liquidity` declaring maker or
  taker. Metadata keys (`ghosalquant.core.metadata_keys`) carry the target weight, stop loss and take profit, order
  time to live, and replacement of resting orders.
- `ExecutionSystem` runs one event loop for backtests, paper and live trading. For each event it expires resting
  orders past their time to live, updates the broker, marks the portfolio, books fills, then sends the strategy's
  signals through the risk manager to the broker.
- Signal sinks (`PrintSink`, `ListSink`, `DataFrameSink`) receive every signal before the risk manager. An
  `ExecutionSystem` with no risk manager, portfolio or broker only emits signals to them.
- Benchmarks (`ghosalquant.bench`): `BenchmarkBuyAndHold` and `BenchmarkTrendVolTarget` (EMA trend with volatility
  targeting).

### Data sources (`ghosalquant.data_layer`)
| Source | Provides |
|---|---|
| `DataFrameDataSource`, `ListDataSource` | Bars from a DataFrame; any events from a list |
| `YFinanceDataSource`, `YFinanceMultiDataSource` | Historical bars from yfinance, one or several symbols |
| `ParquetPolygonDataSource` | Polygon aggregates from a local parquet store, filtered to a session and resampled |
| `LivePolygonDataSource` | Polygon WebSocket: bars, quotes, trades |
| `LiveIBKRDataSource` | IBKR: bars, market depth, tick-by-tick trades, for equities and options |
| `LiveAlpacaDataSource` | Alpaca: bars, top-of-book quotes, trades, for equities and options (`alpaca` extra) |
| `DoltOptionsDataSource`, `CboeEODDataSource`, `NSEDataSource` | End-of-day option chains from DoltHub, Cboe DataShop and NSE bhavcopies |
| `RecordingDataSource`, `RecordedDataSource` | Record a stream to disk as it is consumed; replay a recording |
| `MergedDataSource` (`merge_datasources`), `ShockedDataSource` | Merge streams in time order; replay with shifted prices or thinned depth |

### Brokers (`ghosalquant.brokerage`)
- `SimBroker`: fills against bars, with spread, slippage, market impact, a cap on the share of bar volume, partial
  fills, rejections, latency and per-share commission, with an optional seed.
- `OrderBookSimBroker`: fills against L2 books. A taker sweeps displayed depth; a maker queues behind the displayed
  size at its price and fills when prints trade through it. Maker rebate, taker fee, lot size and order time to
  live are all configurable.
- `IBKRBroker`: paper and live trading through `ib_insync`. Sends market and limit orders for equities and options,
  persists submitted orders across restarts with `state_path`, and returns cash and positions with
  `account_snapshot()`.

### Risk (`ghosalquant.risk`)
`RiskManager` turns a signal into an order through a chain of rules, each of which can resize or block it.

| Purpose | Rules |
|---|---|
| Sizing | `ConfidenceSizer`, `SymbolCapitalFractionSizer`, `FractionalKellySizer`, `SignalQuantityCap` |
| Limits | `MaxOpenPositions`, `MaxSymbolExposure`, `CashAllocationRule`, `DrawdownKillSwitch`, `CorrelationFilter`, `RegimeFilter` |
| Venue | `IntegerQuantity`, `TickRounding` |

### Portfolio and valuation (`ghosalquant.portfolio`)
- `BasicPortfolio`: signed positions, LIFO lots, commissions, de-duplication of fills by `execution_id`, and
  `adopt()` to start from a live account's cash and positions. It refuses a buy fill it can't afford, with a
  warning.
- `PortfolioValuationEngine`: marked NAV, liquidation equity into displayed bids, and a risk equity capped at cost.
- `hedging` gives net delta and gamma per underlier and the hedge quantity; `attribution` splits a position's change
  in value into its causes.

### Contracts and options (`ghosalquant.core`)
- `ContractRegistry` and `Contract`: multiplier, option right, strike, expiry, and cash or physical settlement.
  `settlement_fills` settles positions at expiry.
- `blackscholes` (price, greeks, implied volatility), and `VolSurface` / `Smile` built from a chain.

### Metrics (`ghosalquant.metrics`)
- `risk_adjusted_metrics(equity_curve, init_capital=...)` for any equity curve.
- `evaluate_gates(metrics, spec)` for go/no-go checks on a set of metrics.

### Recording and state
- `ghosalquant.recording`: one durable stream per event kind. Each stream is a write-ahead log sealed into verified
  Parquet segments, with books stored only when they change and prints de-duplicated.
- `ghosalquant.state`: atomic JSON writes, `VersionedJsonStateStore`, and the SQLite `EventLog` / `EventStore`
  journal taken by `ExecutionSystem(journal=...)`.

## Examples
Run as `python examples/<file>`. The yfinance examples plot with `matplotlib` (the `plotting` extra).

| File | Shows |
|---|---|
| [`example_main.py`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/example_main.py) | `DataFrameBacktester` on yfinance AAPL bars with `config.yaml` |
| [`example_comparison.py`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/example_comparison.py) | The example strategy against both benchmarks, equity curves overlaid |
| [`example_order_book_strategy.py`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/example_order_book_strategy.py) | `OrderBookStrategy` on synthetic L2 books and prints with `OrderBookSimBroker` |
| [`example_options_momentum.py`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/example_options_momentum.py) | Options from underlier bars and a quoted chain, with `ContractRegistry`, Kelly sizing and exposure caps |
| [`example_non_backtesting.py`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/example_non_backtesting.py) | `ExecutionSystem` on a live Polygon stream with simulated fills (needs `POLYGON_API_KEY`) |
| [`example_ibkr.py`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/example_ibkr.py) | IBKR live data and paper trading (needs TWS or IB Gateway running) |
| [`config.yaml`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/config.yaml) | Every config section, commented |

## Project Structure
```
src/ghosalquant/
  core/           # Bar, OrderBook, PublicTrade, Signal, Order, Fill, Trade; contracts, Black-Scholes, vol surfaces
  execution/      # Strategy, OrderBookStrategy, StrategyContext, ExecutionSystem, signal sinks
  data_layer/     # Data sources, merging, shocks, recording; Polygon parquet store tools
  backtester/     # Backtester, DataFrameBacktester
  bench/          # Benchmark strategies
  risk/           # RiskManager, risk rules, cash allocation, sizing helpers
  portfolio/      # Portfolio protocol, BasicPortfolio, lots, marks, valuation, hedging, attribution
  brokerage/      # Broker base, SimBroker, OrderBookSimBroker, IBKRBroker, fill and commission models
  metrics/        # Risk-adjusted metrics, performance gates
  recording/      # Recorders and segmented Parquet storage
  state/          # Atomic JSON, versioned state store, SQLite event journal
  util/           # Config, visualisation, KPM calculation, tick rounding
  backtest_ui/    # Prototype GUI (the ui extra)
  compatibility/  # Deprecated LegacyBacktester
examples/         # Example scripts and a commented config.yaml
design/           # User guide, system overview, diagrams
tests/            # pytest suite
```
