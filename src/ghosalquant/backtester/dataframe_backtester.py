import asyncio
from typing import Iterable

from pandas import DataFrame
import pandas as pd

from ..backtester.backtester_base import Backtester
from ..core.types import Order, EquityPoint, BacktestKPMs
from ..execution.strategy_base import Strategy
from ..data_layer.dataframe_datasource import DataFrameDataSource
from ..execution.sinks import DataFrameSink
from ..portfolio.basic_portfolio import BasicPortfolio
from ..util.backtester_utils import output_to_excel
from ..util.config import load_config

class DataFrameBacktester(Backtester):
    def __init__(
            self,
            strategy: Strategy,
            dataframe: DataFrame,
            config_path: str,

            timestamp_col: str | None = None,
            # DataFrame format:
            *,
            symbol: str | None = None,
            open_col: str   = "Open",
            high_col: str   = "High",
            low_col: str    = "Low",
            close_col: str  = "Close",
            volume_col: str = "Volume",

            # Additional Columns
            signal_col: str = "Signal",
            confidence_col: str = "Confidence",
            quantity_col: str = "Quantity",

            # Backtesting
            init_capital: float = 10_000.0
    ) -> None:
        config = load_config(config_path)

        df = dataframe.copy()

        if isinstance(df.columns, pd.MultiIndex): # Flatten MultiIndex if present
            required = {open_col, high_col, low_col, close_col, volume_col}

            levels = [set(map(str, df.columns.get_level_values(i))) for i in range(df.columns.nlevels)]
            level_with_required = None
            for i, vals in enumerate(levels):
                if required.issubset(vals):
                    level_with_required = i
                    break

            if level_with_required is not None:
                df.columns = df.columns.get_level_values(level_with_required)

        # Duplicate the initial DataFrame and initialise signal and confidence columns
        self._return_df = df
        self._return_df[signal_col] = pd.NA
        self._return_df[confidence_col] = 0.0
        self._return_df[quantity_col] = 0.0

        # Backtesting variables
        self._symbols: list[str] = config.system.symbols
        self._portfolio = BasicPortfolio(init_capital=init_capital)

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=df,
                symbol= config.system.symbols[0],
                timestamp_col= timestamp_col,
                open_col= open_col,
                high_col= high_col,
                low_col= low_col,
                close_col= close_col,
                volume_col= volume_col
            ),
            config= config,
            signal_destination=DataFrameSink(
                dataframe= self._return_df,
                timestamp_col= timestamp_col,
                signal_col= signal_col,
                confidence_col= confidence_col,
                quantity_col= quantity_col
            ),
            portfolio= self._portfolio
        )

    async def handle_order(self, order: Order) -> None:
        raise NotImplementedError("DataFrameBacktester does not support handle_order yet, Order sinks are to be implemented.")

    def run(self) -> BacktestKPMs:
        # ExecutionSystem.run() is an async routine so it must be run with asyncio.run() to start the event loop
        asyncio.run(self.execution_system.run())

        #kpms: BacktestKPMs = calculate_stats(df_results=self._return_df)
        #output_to_excel(df_results=self._return_df)

        return self.kpms

    @property
    def dataframe(self) -> DataFrame:
        return self._return_df

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

    from ..core.types import BacktestKPMs

    @property
    def kpms(self) -> BacktestKPMs:
        trades = list(self._portfolio.closed_trades)

        total_trades = len(trades)

        wins = 0
        losses = 0
        for t in trades:
            if (t.realised_pnl or 0.0) > 0.0: # Default to 0.0 if None
                wins += 1
            elif (t.realised_pnl or 0.0) < 0.0:
                losses += 1

        if total_trades > 0:
            win_rate = (wins / total_trades) * 100.0
        else:
            win_rate = 0.0

        return BacktestKPMs(
            total_trades=total_trades,
            total_wins=wins,
            total_losses=losses,
            win_rate_pct=win_rate,
            realised_pnl=self.realised_pnl,
            unrealised_pnl=self.unrealised_pnl,
            total_pnl=self.total_pnl
        )
