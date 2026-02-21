import asyncio
from abc import ABC, abstractmethod
from typing import final, Iterable

from ..data_layer.datasource_factory import datasource_from_config
from ..execution.strategy_base import Strategy
from ..execution.execution_system import ExecutionSystem
from ..data_layer.datasource_base import DataSource
from ..execution.sinks import SignalSink
from ..core.types import Order, BacktestKPMs, EquityPoint
from ..portfolio.basic_portfolio import BasicPortfolio
from ..portfolio.portfolio_base import Portfolio
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
            config: Config | None = None
    ) -> None:
        self.config = config

        if portfolio is None:
            portfolio = BasicPortfolio(init_capital=10_000.0)

        if risk_manager is None and config is not None and config.system.use_risk_manager:
            risk_manager = build_risk_manager(config.risk)

        self.execution_system = ExecutionSystem(
            data_src = data_src,
            strategy = strategy,
            signal_destination = signal_destination,
            risk_manager = risk_manager,
            portfolio = portfolio
        )

        self._portfolio = self.execution_system.portfolio

    @classmethod
    def from_config(cls,
                    *,
                    strategy: Strategy,
                    config_path: str,

                    # Optional overrides
                    signal_destination: SignalSink | None = None,
                    portfolio: Portfolio | None = None,
                    risk_manager: RiskManager | None = None,
                    init_capital: float = 10_000.0,
        ) -> "Backtester":

            cfg = load_config(config_path)
            data_src = datasource_from_config(cfg)

            if portfolio is None:
                portfolio = BasicPortfolio(init_capital=float(init_capital))

            if risk_manager is None and cfg.system.use_risk_manager:
                risk_manager = build_risk_manager(cfg.risk)

            return cls(
                strategy=strategy,
                data_src=data_src,
                signal_destination=signal_destination,
                portfolio=portfolio,
                risk_manager=risk_manager,
                config=cfg
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
        """Calculate and return key performance metrics after completing a backtest."""
        return calculate_kpms(self._portfolio)

    @final
    async def run_async(self) -> BacktestKPMs:
        await self.execution_system.run()
        return calculate_kpms(self._portfolio)

    @final
    def run(self) -> BacktestKPMs:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.run_async())

        raise RuntimeError("Cannot run backtest synchronously within an existing event loop. Please use run_async() instead.")
