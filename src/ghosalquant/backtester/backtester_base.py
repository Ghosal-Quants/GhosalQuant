import asyncio
from pathlib import Path
from typing import final, Iterable

from ..data_layer.datasource_factory import datasource_from_config
from ..brokerage.builder import build_broker
from ..execution.strategy_base import Strategy
from ..execution.execution_system import ExecutionSystem
from ..data_layer.datasource_base import DataSource
from ..execution.sinks import SignalSink
from ..core.contracts import ContractRegistry
from ..core.types import Order, BacktestKPMs, EquityPoint
from ..portfolio.basic_portfolio import BasicPortfolio
from ..portfolio.portfolio_base import Portfolio
from ..brokerage.broker_base import Broker
from ..risk.builder import build_risk_manager
from ..risk.risk_manager import RiskManager
from ..util.backtester_utils import calculate_kpms
from ..util.config import Config, load_config


class Backtester:
    def __init__(
            self,
            *,
            strategy: Strategy,
            data_src: DataSource,
            signal_destination: SignalSink | None = None,
            portfolio: Portfolio | None = None,
            risk_manager: RiskManager | None = None,
            broker: Broker | None = None,
            config: Config | None = None,
            contracts: ContractRegistry | None = None,
    ) -> None:
        self.config = config

        if portfolio is None:
            portfolio = BasicPortfolio(init_capital=10_000.0, contracts=contracts)

        if risk_manager is None:
            risk_manager = _risk_manager_for(config)

        self.execution_system = ExecutionSystem(
            data_src = data_src,
            strategy = strategy,
            signal_destination = signal_destination,
            risk_manager = risk_manager,
            portfolio = portfolio,
            broker = broker,
            contracts = contracts,
        )

        self._portfolio = self.execution_system.portfolio

    @classmethod
    def from_config(cls,
                    *,
                    strategy: Strategy,
                    config_path: str | None = None,
                    config: Config | None = None,

                    # Optional overrides
                    signal_destination: SignalSink | None = None,
                    portfolio: Portfolio | None = None,
                    risk_manager: RiskManager | None = None,
                    init_capital: float = 10_000.0,
                    contracts: ContractRegistry | None = None,
        ) -> "Backtester":

            if config is None and config_path is None:
                raise TypeError("from_config() needs config= or config_path=")
            cfg = _resolve_config(config, config_path)
            data_src = datasource_from_config(cfg, contracts=contracts)

            if portfolio is None:
                portfolio = BasicPortfolio(init_capital=float(init_capital), contracts=contracts)

            return cls(
                strategy=strategy,
                data_src=data_src,
                signal_destination=signal_destination,
                portfolio=portfolio,
                risk_manager=risk_manager,
                broker=build_broker(cfg.broker, contracts=contracts),
                config=cfg,
                contracts=contracts,
            )

    # ----- Properties ----- #
    @property
    def equity_curve(self) -> Iterable[EquityPoint]:
        return self._portfolio.equity_curve

    @property
    def realised_pnl(self) -> float:
        return self._portfolio.realised_pnl

    @property
    def unrealised_pnl(self) -> float:
        return self._portfolio.unrealised_pnl

    @property
    def total_pnl(self) -> float:
        return self.realised_pnl + self.unrealised_pnl

    @property
    def kpms(self) -> BacktestKPMs:
        if not self._portfolio:
            raise ValueError("Cannot produce KPMs without an initialised Portfolio")

        """Calculate and return key performance metrics after completing a backtest."""
        return calculate_kpms(self._portfolio)

    @final
    async def run_async(self) -> BacktestKPMs:
        await self.execution_system.run()

        if not self._portfolio:
            raise ValueError("Cannot produce KPMs without an initialised Portfolio")

        return calculate_kpms(self._portfolio)

    @final
    def run(self) -> BacktestKPMs:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async())

        raise RuntimeError("Cannot run backtest synchronously within an existing event loop. Please use run_async() instead.")


def _resolve_config(config: Config | None, config_path: str | Path | None) -> Config:
    """The ``Config`` object if given, else the YAML file, else the defaults."""
    if config is not None and config_path is not None:
        raise ValueError("Pass config or config_path, not both.")
    if config is not None:
        return config
    if config_path is not None:
        return load_config(config_path)
    return Config()


def _risk_manager_for(config: Config | None) -> RiskManager:
    # A Portfolio needs a RiskManager, so with the flag off this one applies no rules.
    if config is not None and config.system.use_risk_manager:
        return build_risk_manager(config.risk)
    return RiskManager(rules=[])
