# GhosalQuant
Ghosal Fund | Quantitative Trading Team Repository

A modular Python framework for developing, backtesting, and executing quantitative trading strategies.

## Documentation
- [System Overview](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/design/system-overview.md) - architecture + data flow
- [User Guide](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/design/user_guide.md) - getting started + quick reference

## Installation

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
[`.env.example`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/.env.example)).

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
1. **Write a strategy** — subclass `Strategy`, implement `on_market_data(bar) -> Signal | None`
2. **Create a config** — see [`examples/config.yaml`](https://github.com/Ghosal-Quants/GhosalQuant/blob/main/examples/config.yaml)
3. **Run a backtest**:
```python
from ghosalquant.backtester.backtester_base import Backtester
from my_strategy import MyStrategy

kpms = Backtester.from_config(strategy=MyStrategy(), config_path="config.yaml").run()
print(kpms)
```

See [`examples/`](https://github.com/Ghosal-Quants/GhosalQuant/tree/main/examples) for more examples for: DataFrame
backtesting, strategy comparison, IBKR paper trading, and live data.

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