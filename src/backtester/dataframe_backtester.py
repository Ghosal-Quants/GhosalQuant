import asyncio
from datetime import datetime

from pandas import DataFrame

from backtester.backtester import Backtester
from core.types import SignalType, Signal, Trade, EquityPoint
from execution.strategy_base import Strategy
from data_layer.dataframe_datasource import DataFrameDataSource
from execution.sinks import DataFrameSink, BacktesterSink
from util.backtester_utils import calculate_stats


class DataFrameBacktester(Backtester):
    def __init__(
            self,
            strategy: Strategy,
            dataframe: DataFrame,

            timestamp_col: str | None = None,
            # DataFrame format:
            *,
            symbol: str,
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
            init_capital: int = 10000
    ) -> None:
        # Duplicate the initial DataFrame and initialise signal and confidence columns
        self._return_df = dataframe.copy()
        self._return_df[signal_col] = SignalType.NOOP.value
        self._return_df[confidence_col] = 0.0
        self._return_df[quantity_col] = 0.0

        # Backtesting variables
        self._next_trade_id: int = 0 # start indexing trades from 0
        self._init_capital = init_capital

        # Trades are uniquely identified by their id field but also by their symbol (cant close a trade of another symbol)
        # Symbol -> Trade ID -> Trade
        self._open_trades: dict[str, dict[int, Trade]] = {}
        self._closed_trades: list[Trade] = []

        # Pnl tracking and equity points
        self._realised_pnl: float = 0.0
        self._equity_points: list[EquityPoint] = []

        super().__init__(
            strategy=strategy,
            data_src=DataFrameDataSource(
                dataframe=dataframe,
                symbol= symbol,
                timestamp_col= timestamp_col,
                open_col= open_col,
                high_col= high_col,
                low_col= low_col,
                close_col= close_col,
                volume_col= volume_col
            ),
            signal_destination=DataFrameSink(
                dataframe= self._return_df,
                timestamp_col= timestamp_col,
                signal_col= signal_col,
                confidence_col= confidence_col,
                quantity_col= quantity_col,
                next_sink=BacktesterSink(self)
            )
        )

    def get_return_dataframe(self) -> DataFrame:
        return self._return_df

    async def handle_signal(self, signal: Signal) -> None:
        if signal.signal_type == SignalType.NOOP:
            return

        if signal.quantity is None:
            raise ValueError("Signal quantity cannot be None")

        if signal.symbol not in self._open_trades:
            self._open_trades[signal.symbol] = {}

        # Open trade (BUY)
        if signal.signal_type == SignalType.BUY:
            trade_id: int = self._next_trade_id
            self._next_trade_id += 1

            trade = Trade(
                id=trade_id,
                symbol=signal.symbol,
                signal_type=signal.signal_type,
                entry_price=signal.price,
                size=signal.quantity if signal.quantity is not None else 1,
                open_time=signal.time_stamp
            )
            self._open_trades[signal.symbol][trade.id] = trade
            return

        # Close trade (SELL)
        if signal.signal_type == SignalType.SELL:
            if signal.symbol not in self._open_trades or not self._open_trades[signal.symbol]:
                # Ideally this should raise an error, but strategies might not be that smart
                # raise RuntimeError(f"SELL signal received for {symbol}, but no open trades exist.")
                return

            trade_id = next(reversed(self._open_trades[signal.symbol]))
            trade = self._open_trades[signal.symbol].pop(trade_id)

            trade.close_time = signal.time_stamp
            trade.realised_pnl = trade.unrealised_pnl(signal.price)

            self._realised_pnl += trade.realised_pnl
            self._closed_trades.append(trade) # Keep track of closed trades

            self._snapshot_equity(timestamp=signal.time_stamp, current_price=signal.price)
            return

        # In case new signal types are added in the future which need handling
        raise TypeError(f"Unknown signal type: {signal.signal_type}")

    def run(self) -> DataFrame:
        # ExecutionSystem.run() is an async routine so it must be run
        # with asyncio.run() to start the event loop
        asyncio.run(self.execution_system.run())

        #if self.df_results is not None:
        #    calculate_stats(df_results=self.df_results)
        #    output_to_excel(df_results=self.df_results)

        return self._return_df

    # Helper: Snapshot of current equity
    # There might be a more efficient way to do this?
    def _snapshot_equity(self, timestamp: datetime, current_price: float) -> None:
        unrealised_pnl: float = 0.0

        for trades in self._open_trades.values():
            for trade in trades.values():
                unrealised_pnl += trade.unrealised_pnl(current_price)

        equity = self._init_capital + self._realised_pnl + unrealised_pnl

        self._equity_points.append(
            EquityPoint(
                time_stamp= timestamp,
                equity= equity,
                realised_pnl= self._realised_pnl,
                unrealised_pnl= unrealised_pnl,
                num_open_trades=sum(len(t) for t in self._open_trades.values()),
            )
        )
