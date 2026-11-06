# GhosalQuant
Ghosal Fund | Quantitative Trading Team Repository

A modular Python framework for developing, backtesting, and executing quantitative trading strategies.

## Documentation
- [System Overview](design/system-overview.md) - architecture + data flow
- [User Guide](design/user_guide.md) - getting started + quick reference

## Installation
```bash
git clone https://github.com/tomlaustin/GhosalQuant.git
cd GhosalQuant
pip install -e .
```

## Quick Start
1. **Write a strategy** — subclass `Strategy`, implement `on_market_data(bar) -> Signal | None`
2. **Create a config** — see `examples/config.yaml`
3. **Run a backtest**:
```python
from ghosalquant.backtester.backtester_base import Backtester
from my_strategy import MyStrategy

kpms = Backtester.from_config(strategy=MyStrategy(), config_path="config.yaml").run()
print(kpms)
```

See `examples/` for more examples for: DataFrame backtesting, strategy comparison, IBKR paper trading, and live data.

## Project Structure
```
src/ghosalquant/
  core/           # Bar, Signal, Order, Fill, Trade types
  execution/      # Strategy base, ExecutionSystem, signal sinks, benchmarks
  data_layer/     # DataSource implementations (yfinance, Polygon, DataFrame, list)
  backtester/     # Backtester, DataFrameBacktester
  risk/           # RiskManager, rules (MaxOpenPositions, ConfidenceSizer)
  portfolio/      # Portfolio protocol, BasicPortfolio
  brokerage/      # Broker base, SimBroker, IBKRBroker
  util/           # Config, visualisation, helpers
  backtest_ui/    # Prototype GUI (the [ui] extra)
examples/         # Ready-to-run scripts and sample config
```

## Tom's Notes

### What are we actually trying to do?
- Find ideas for interesting strategies for a given asset class
- Implement the mathematical theory algorithmically
- Provide quantitative evidence that they work, over a period of time
- Execute the orders

### Some existing open source frameworks
- https://amor71.github.io/LiuAlgoTrader/
- https://www.backtrader.com/
- https://github.com/gbeced/basana (crypto orientated)
- https://github.com/panpanpandas/ultrafinance
- https://github.com/ematvey/pybacktest
- https://github.com/mhallsmoore/qstrader