import asyncio
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from ghosalquant import Strategy
from ghosalquant.backtester import Backtester, DataFrameBacktester
from ghosalquant.core import metadata_keys
from ghosalquant.core.types import Fill, Signal, SignalType
from ghosalquant.portfolio.basic_portfolio import BasicPortfolio
from ghosalquant.util.config import BrokerConfig, Config, ConfigError, SimBrokerConfig

# No spread, slippage or impact, so fills land exactly on the bar price.
EXACT = Config(broker=BrokerConfig(sim=SimBrokerConfig(
    spread_bps=0.0, slippage_mean_bps=0.0, slippage_std_bps=0.0, impact_bps_at_full_participation=0.0,
)))


class BuyOnce(Strategy):
    def __init__(self, *, quantity: float | None = None, weight: float | None = None) -> None:
        super().__init__()
        self.quantity = quantity
        self.weight = weight
        self.symbols: set[str] = set()

    def on_start(self) -> None:
        self.done = False

    def on_end(self) -> None:
        pass

    def on_market_data(self, bar):
        self.symbols.add(bar.symbol)
        if self.done:
            return None
        self.done = True
        metadata = None if self.weight is None else {metadata_keys.TARGET_WEIGHT: self.weight}
        return Signal(
            time_stamp=bar.time_stamp, symbol=bar.symbol, signal_type=SignalType.BUY,
            ref_price=bar.close, quantity=self.quantity, metadata=metadata,
        )


def bars(*closes: float) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-02", periods=len(closes))
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1e6}, index=index,
    )


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text)
    return path


SYSTEM_ONLY = """
SYSTEM:
  symbols: TEST
  main_timeframe: 1D
  execution_timeframe: 1D
"""


def test_a_dataframe_backtest_needs_no_config():
    strategy = BuyOnce(quantity=10.0)
    kpms = DataFrameBacktester(strategy=strategy, dataframe=bars(100.0, 101.0, 102.0)).run()

    assert strategy.symbols == {"ASSET"}
    assert kpms.fills == 1
    assert kpms.open_positions == 1


def test_symbol_names_the_dataframe_bars(tmp_path):
    strategy = BuyOnce(quantity=10.0)
    DataFrameBacktester(
        strategy=strategy, dataframe=bars(100.0, 101.0), config_path=write(tmp_path, SYSTEM_ONLY),
        symbol="SPY",
    ).run()

    assert strategy.symbols == {"SPY"}


def test_without_symbol_the_first_configured_one_is_used(tmp_path):
    strategy = BuyOnce(quantity=10.0)
    DataFrameBacktester(
        strategy=strategy, dataframe=bars(100.0, 101.0), config_path=write(tmp_path, SYSTEM_ONLY),
    ).run()

    assert strategy.symbols == {"TEST"}


def test_a_config_object_configures_the_run():
    backtester = DataFrameBacktester(
        strategy=BuyOnce(quantity=10.0), dataframe=bars(100.0, 101.0, 102.0), config=EXACT,
    )
    backtester.run()

    assert [trade.entry_price for trade in backtester.execution_system.portfolio.open_trades] == [101.0]


def test_use_risk_manager_false_runs_without_the_risk_rules(tmp_path):
    path = write(tmp_path, SYSTEM_ONLY + """  use_risk_manager: false
RISK:
  rules:
    - name: MaxOpenPositions
      max_open_positions: 0
""")
    kpms = DataFrameBacktester(
        strategy=BuyOnce(quantity=10.0), dataframe=bars(100.0, 101.0), config_path=path,
    ).run()

    assert kpms.fills == 1


def test_config_and_config_path_are_not_both_taken(tmp_path):
    with pytest.raises(ValueError, match="not both"):
        DataFrameBacktester(
            strategy=BuyOnce(), dataframe=bars(100.0), config=Config(),
            config_path=write(tmp_path, SYSTEM_ONLY),
        )


def test_from_config_needs_a_data_source():
    with pytest.raises(TypeError, match="config"):
        Backtester.from_config(strategy=BuyOnce())
    with pytest.raises(ConfigError, match="DataFrameBacktester"):
        Backtester.from_config(strategy=BuyOnce(), config=Config())


def test_a_full_target_weight_enters_just_under_sized():
    # Sized at 100 and filled at 100.5: all of the $10,000 would no longer pay for 100 shares.
    backtester = DataFrameBacktester(
        strategy=BuyOnce(weight=1.0), dataframe=bars(100.0, 100.5, 101.0), config=EXACT,
    )
    kpms = backtester.run()

    assert kpms.fills == 1
    assert backtester.execution_system.portfolio.open_qty_by_symbol["ASSET"] == pytest.approx(99.0)


def test_an_unsized_signal_warns_instead_of_vanishing(caplog):
    with caplog.at_level(logging.WARNING, logger="ghosalquant"):
        kpms = DataFrameBacktester(strategy=BuyOnce(), dataframe=bars(100.0, 101.0)).run()

    assert kpms.fills == 0
    assert "no quantity or TARGET_WEIGHT" in caplog.text


def test_a_fill_beyond_the_cash_balance_is_booked_and_counted():
    portfolio = BasicPortfolio(init_capital=1_000.0)
    fill = Fill(
        time_stamp=datetime(2024, 1, 2), symbol="TEST", side=SignalType.BUY, quantity=20.0, price=100.0,
    )

    asyncio.run(portfolio.process_fill(fill))

    assert portfolio.cash_balance == -1_000.0
    assert portfolio.fill_count == 1


def test_kpms_count_a_held_position_and_its_fill():
    kpms = DataFrameBacktester(strategy=BuyOnce(quantity=10.0), dataframe=bars(100.0, 101.0, 102.0)).run()

    # Nothing closed, so no trades, but the position and its fill are there.
    assert kpms.total_trades == 0
    assert (kpms.open_positions, kpms.fills) == (1, 1)
    assert "Open Positions: 1" in str(kpms)
