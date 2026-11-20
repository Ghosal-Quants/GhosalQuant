from __future__ import annotations

import asyncio
import dataclasses
import math
from typing import Any

import pandas as pd

from .. import __version__
from ..backtester.dataframe_backtester import DataFrameBacktester
from ..util.config import BrokerConfig, Config, SimBrokerConfig
from .schema import SpecError, validate_spec
from .strategy import BlockStrategy

MAX_BARS = 20_000
_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def bars_from_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """
    Bars from the compact column form a data relay serves::

        {"t": [unix seconds, ...], "o": [...], "h": [...], "l": [...], "c": [...], "v": [...]}

    Rows with a missing price are dropped; a missing volume counts as zero.
    """
    try:
        frame = pd.DataFrame(
            {
                "Open": payload["o"], "High": payload["h"], "Low": payload["l"],
                "Close": payload["c"], "Volume": payload["v"],
            },
            index=pd.to_datetime(payload["t"], unit="s"),
            dtype="float64",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise SpecError("data", f"the price data is not in the expected form ({error})") from None
    frame["Volume"] = frame["Volume"].fillna(0.0)
    return frame.dropna(subset=["Open", "High", "Low", "Close"])


async def run_spec_async(spec: dict[str, Any], bars: pd.DataFrame) -> dict[str, Any]:
    """
    Backtest a block-strategy spec on ``bars`` (Open, High, Low, Close, Volume columns and a
    datetime index) and return a JSON-ready result: metrics, the equity curve beside buy-and-hold,
    and every fill. Raises ``SpecError`` for a spec or data the run cannot use.

    The simulated broker is seeded, so the same spec on the same bars gives the same result.
    """
    strategy = BlockStrategy(spec)
    spec = strategy.spec
    frame = _prepare(bars, spec)
    capital = spec["initial_capital"]

    config = Config(broker=BrokerConfig(sim=SimBrokerConfig(
        spread_bps=spec["costs"]["spread_bps"],
        commission_per_share=spec["costs"]["commission_per_share"],
        seed=0,
    )))
    backtester = DataFrameBacktester(
        strategy=strategy, dataframe=frame, config=config, symbol=spec["symbol"], init_capital=capital,
    )
    kpms = await backtester.run_async()

    first_close = float(frame["Close"].iloc[0])
    closes = {ts.to_pydatetime(): float(close) for ts, close in frame["Close"].items()}
    equity = []
    peak = capital
    max_drawdown_pct = 0.0
    last_equity = capital
    for point in backtester.equity_curve:
        close = closes.get(point.time_stamp)
        last_equity = float(point.equity)
        peak = max(peak, last_equity)
        if peak > 0.0:
            max_drawdown_pct = max(max_drawdown_pct, (1.0 - last_equity / peak) * 100.0)
        equity.append({
            "t": point.time_stamp.date().isoformat(),
            "strategy": _clean(last_equity),
            "buy_hold": _clean(capital * close / first_close) if close is not None else None,
        })

    metrics = {key: _clean(value) for key, value in dataclasses.asdict(kpms).items()}
    metrics["final_equity"] = _clean(last_equity)
    metrics["total_return_pct"] = _clean((last_equity / capital - 1.0) * 100.0)
    metrics["max_drawdown_pct"] = _clean(max_drawdown_pct)
    metrics["buy_hold_return_pct"] = _clean((float(frame["Close"].iloc[-1]) / first_close - 1.0) * 100.0)

    return {
        "spec_version": spec["version"],
        "engine_version": __version__,
        "symbol": spec["symbol"],
        "start": frame.index[0].date().isoformat(),
        "end": frame.index[-1].date().isoformat(),
        "bars": int(len(frame)),
        "initial_capital": capital,
        "metrics": metrics,
        "equity": equity,
        "trades": [
            {
                "t": fill.time_stamp.date().isoformat(),
                "side": fill.side.value,
                "quantity": _clean(fill.quantity),
                "price": _clean(fill.price),
            }
            for fill in strategy.fills
        ],
    }


def run_spec(spec: dict[str, Any], bars: pd.DataFrame) -> dict[str, Any]:
    """``run_spec_async`` for callers with no event loop running. In a browser, await the async form."""
    return asyncio.run(run_spec_async(spec, bars))


def _prepare(bars: pd.DataFrame, spec: dict[str, Any]) -> pd.DataFrame:
    missing = [column for column in _COLUMNS if column not in bars.columns]
    if missing:
        raise SpecError("data", f"the price data has no {', '.join(missing)} column")
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise SpecError("data", "the price data needs a datetime index")

    frame = bars.loc[:, list(_COLUMNS)].sort_index()
    if frame.index.tz is not None:
        frame.index = frame.index.tz_localize(None)
    frame = frame[~frame.index.duplicated(keep="last")]
    if spec["start"]:
        frame = frame[frame.index >= pd.Timestamp(spec["start"])]
    if spec["end"]:
        frame = frame[frame.index <= pd.Timestamp(spec["end"])]

    if len(frame) < 2:
        raise SpecError("data", f"there is no price data for {spec['symbol']} in that date range")
    if len(frame) > MAX_BARS:
        raise SpecError("data", f"the date range holds more than {MAX_BARS} bars")
    return frame


def _clean(value: Any) -> Any:
    """Numbers JSON can carry: NaN and infinity become ``None``."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
