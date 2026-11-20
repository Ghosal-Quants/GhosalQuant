import copy
import json
import subprocess
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from ghosalquant import Bar, Signal, SignalType, Strategy, metadata_keys
from ghosalquant.backtester import DataFrameBacktester
from ghosalquant.blocks import BlockStrategy, SpecError, bars_from_payload, run_spec, validate_spec
from ghosalquant.blocks.nodes import BarContext, compile_node
from ghosalquant.util.config import BrokerConfig, Config, SimBrokerConfig

CLOSE = {"type": "price", "field": "close"}


def _bars(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, n)))
    open_ = np.r_[100.0, close[:-1]] * np.exp(rng.normal(0.0, 0.003, n))
    return pd.DataFrame(
        {
            "Open": open_,
            "High": np.maximum(open_, close) * 1.002,
            "Low": np.minimum(open_, close) * 0.998,
            "Close": close,
            "Volume": 1e6,
        },
        index=pd.bdate_range("2015-01-02", periods=n),
    )


def _indicator(name: str, period: int, **extra) -> dict:
    return {"type": "indicator", "name": name, "period": period, **extra}


def _trend_spec(period: int = 50) -> dict:
    sma = _indicator("sma", period)
    return {
        "version": 1,
        "symbol": "DEMO",
        "rules": [
            {"when": {"type": "compare", "op": ">", "left": CLOSE, "right": sma},
             "then": {"type": "set_position", "percent": 100}},
            {"when": {"type": "compare", "op": "<=", "left": CLOSE, "right": sma},
             "then": {"type": "set_position", "percent": 0}},
        ],
    }


def _series(node: dict, frame: pd.DataFrame) -> list:
    """The node's value on each bar of ``frame``, after the validator has filled in its defaults."""
    is_condition = node["type"] in ("compare", "cross", "and", "or", "not", "in_position")
    when = node if is_condition else {"type": "compare", "op": ">", "left": node, "right": {"type": "number", "value": 0}}
    spec = validate_spec({"version": 1, "symbol": "DEMO", "rules": [{"when": when, "then": {"type": "set_position", "percent": 100}}]})
    normalised = spec["rules"][0]["when"]
    compiled = compile_node(normalised if is_condition else normalised["left"])
    out = []
    for ts, row in frame.iterrows():
        bar = Bar(ts.to_pydatetime(), "DEMO", row.Open, row.High, row.Low, row.Close, row.Volume)
        out.append(compiled.step(BarContext(bar, False, None, None)))
    return out


def _assert_matches(values: list, reference: pd.Series) -> None:
    got = np.array([np.nan if v is None else v for v in values], dtype=float)
    np.testing.assert_allclose(got, reference.to_numpy(dtype=float), rtol=1e-9, atol=1e-9, equal_nan=True)


# ----- Validation ----- #

def test_a_valid_spec_is_normalised_with_defaults():
    spec = validate_spec({**_trend_spec(), "symbol": " aapl "})

    assert spec["symbol"] == "AAPL"
    assert spec["initial_capital"] == 10_000.0
    assert spec["costs"] == {"spread_bps": 2.0, "commission_per_share": 0.0}
    assert spec["rules"][0]["when"]["right"]["source"] == CLOSE


@pytest.mark.parametrize("mutate, path", [
    (lambda s: s.update(version=2), "version"),
    (lambda s: s.update(symbol="not a ticker!"), "symbol"),
    (lambda s: s.update(rules=[]), "rules"),
    (lambda s: s.update(start="2020-01-01", end="2019-01-01"), "end"),
    (lambda s: s.update(initial_capital=True), "initial_capital"),
    (lambda s: s.update(extra=1), "extra"),
    (lambda s: s["rules"][0]["then"].update(percent=150), "rules[0].then.percent"),
    (lambda s: s["rules"][0]["when"].update(op="=="), "rules[0].when.op"),
    (lambda s: s["rules"][0]["when"].update(left=None), "rules[0].when.left"),
    (lambda s: s["rules"][0]["when"]["right"].update(period=0), "rules[0].when.right.period"),
    (lambda s: s["rules"][0]["when"]["right"].update(period=2.5), "rules[0].when.right.period"),
    (lambda s: s["rules"][0]["when"]["right"].update(name="magic"), "rules[0].when.right.name"),
    (lambda s: s["rules"][0]["when"].update(left={"type": "in_position"}), "rules[0].when.left.type"),
    (lambda s: s["rules"][0].update(when=CLOSE), "rules[0].when.type"),
])
def test_an_invalid_spec_names_where_it_is_wrong(mutate, path):
    spec = copy.deepcopy(_trend_spec())
    mutate(spec)

    with pytest.raises(SpecError) as error:
        validate_spec(spec)
    assert error.value.path == path


def test_a_spec_nested_too_deep_or_too_large_is_refused():
    deep = CLOSE
    for _ in range(20):
        deep = {"type": "math", "op": "+", "left": deep, "right": {"type": "number", "value": 1}}
    spec = _trend_spec()
    spec["rules"][0]["when"]["left"] = deep
    with pytest.raises(SpecError, match="nested"):
        validate_spec(spec)

    wide = _trend_spec()
    condition = wide["rules"][0]["when"]
    wide["rules"] = [
        {"when": {"type": "and", "args": [condition] * 8}, "then": {"type": "set_position", "percent": 100}}
    ] * 20
    with pytest.raises(SpecError, match="blocks"):
        validate_spec(wide)


# ----- Indicators against pandas ----- #

def test_windowed_indicators_match_pandas():
    frame = _bars(300)
    close = frame["Close"]

    _assert_matches(_series(_indicator("sma", 20), frame), close.rolling(20).mean())
    _assert_matches(_series(_indicator("highest", 20, source={"type": "price", "field": "high"}), frame),
                    frame["High"].rolling(20).max())
    _assert_matches(_series(_indicator("lowest", 20, source={"type": "price", "field": "low"}), frame),
                    frame["Low"].rolling(20).min())
    _assert_matches(_series(_indicator("stddev", 20), frame), close.rolling(20).std(ddof=0))
    _assert_matches(_series(_indicator("roc", 10), frame), close.pct_change(10) * 100.0)
    _assert_matches(_series(_indicator("bollinger_upper", 20, mult=2.5), frame),
                    close.rolling(20).mean() + 2.5 * close.rolling(20).std(ddof=0))
    _assert_matches(_series(_indicator("bollinger_lower", 20), frame),
                    close.rolling(20).mean() - 2.0 * close.rolling(20).std(ddof=0))


def test_ema_is_seeded_with_the_sma_then_smooths():
    frame = _bars(120)
    close = frame["Close"].to_numpy()
    period = 12

    expected = [np.nan] * (period - 1) + [close[:period].mean()]
    for x in close[period:]:
        expected.append(expected[-1] + (2.0 / (period + 1)) * (x - expected[-1]))

    _assert_matches(_series(_indicator("ema", period), frame), pd.Series(expected))


def test_rsi_and_atr_use_wilder_smoothing():
    frame = _bars(200)
    period = 14

    change = frame["Close"].diff()
    gains = change.clip(lower=0.0).to_numpy()[1:]
    losses = (-change).clip(lower=0.0).to_numpy()[1:]
    rsi = [np.nan] * period
    gain, loss = gains[:period].mean(), losses[:period].mean()
    rsi.append(100.0 - 100.0 / (1.0 + gain / loss))
    for g, l in zip(gains[period:], losses[period:]):
        gain += (g - gain) / period
        loss += (l - loss) / period
        rsi.append(100.0 - 100.0 / (1.0 + gain / loss))
    _assert_matches(_series(_indicator("rsi", period), frame), pd.Series(rsi))

    previous_close = frame["Close"].shift(1)
    true_range = pd.concat(
        [frame["High"] - frame["Low"], (frame["High"] - previous_close).abs(), (frame["Low"] - previous_close).abs()],
        axis=1,
    ).max(axis=1).to_numpy()
    atr = [np.nan] * (period - 1) + [true_range[:period].mean()]
    for value in true_range[period:]:
        atr.append(atr[-1] + (value - atr[-1]) / period)
    _assert_matches(_series(_indicator("atr", period), frame), pd.Series(atr))


def test_previous_math_and_cross():
    frame = _bars(60)
    close = frame["Close"]

    _assert_matches(_series({"type": "previous", "of": CLOSE, "bars": 3}, frame), close.shift(3))
    _assert_matches(
        _series({"type": "math", "op": "/", "left": CLOSE, "right": {"type": "number", "value": 0}}, frame),
        pd.Series([np.nan] * len(frame)),
    )

    fast, slow = close.rolling(3).mean(), close.rolling(10).mean()
    crossed = _series({"type": "cross", "direction": "above",
                       "left": _indicator("sma", 3), "right": _indicator("sma", 10)}, frame)
    expected = ((fast > slow) & (fast.shift(1) <= slow.shift(1))).to_numpy()
    assert crossed == list(expected)


# ----- Behaviour as a strategy ----- #

class _HandWrittenTrend(Strategy):
    """The trend spec, written the ordinary way, to check the interpreter against."""

    def __init__(self, period: int) -> None:
        super().__init__()
        self.period = period

    def on_start(self) -> None:
        self.closes: list[float] = []
        self.target: float | None = None

    def on_end(self) -> None:
        pass

    def on_market_data(self, bar: Bar) -> Signal | None:
        self.closes.append(bar.close)
        if len(self.closes) < self.period:
            return None
        weight = 1.0 if bar.close > np.mean(self.closes[-self.period:]) else 0.0
        if weight == self.target:
            return None
        self.target = weight
        return Signal(
            time_stamp=bar.time_stamp, symbol=bar.symbol, ref_price=bar.close,
            signal_type=SignalType.BUY if weight else SignalType.SELL,
            metadata={metadata_keys.TARGET_WEIGHT: weight},
        )


def test_a_spec_gives_the_same_result_as_the_hand_written_strategy():
    frame = _bars()
    config = Config(broker=BrokerConfig(sim=SimBrokerConfig(seed=0)))

    by_hand = DataFrameBacktester(strategy=_HandWrittenTrend(50), dataframe=frame, config=config, symbol="DEMO")
    by_hand_kpms = by_hand.run()
    result = run_spec(_trend_spec(50), frame)

    assert result["metrics"]["fills"] == by_hand_kpms.fills > 0
    assert result["metrics"]["total_trades"] == by_hand_kpms.total_trades
    assert result["metrics"]["total_pnl"] == pytest.approx(by_hand_kpms.total_pnl, rel=1e-9)
    assert [p["strategy"] for p in result["equity"]] == pytest.approx([p.equity for p in by_hand.equity_curve])


def test_signals_never_depend_on_later_bars():
    frame = _bars(400)
    spec = _trend_spec(20)
    spec["rules"].insert(0, {
        "when": {"type": "cross", "direction": "below", "left": _indicator("rsi", 14), "right": {"type": "number", "value": 70}},
        "then": {"type": "set_position", "percent": 50},
    })

    def signals(data: pd.DataFrame) -> list:
        strategy = BlockStrategy(spec)
        strategy.on_start()
        out = []
        for ts, row in data.iterrows():
            signal = strategy.on_market_data(
                Bar(ts.to_pydatetime(), "DEMO", row.Open, row.High, row.Low, row.Close, row.Volume)
            )
            out.append(None if signal is None else signal.metadata[metadata_keys.TARGET_WEIGHT])
        return out

    full = signals(frame)
    # Rewriting the future must leave every earlier decision as it was.
    tampered = frame.copy()
    tampered.iloc[250:, :4] *= 3.0
    assert signals(tampered)[:250] == full[:250]
    assert signals(frame.iloc[:250]) == full[:250]
    assert any(weight is not None for weight in full)


def test_a_rule_that_stays_true_signals_once_and_position_state_tracks_fills():
    spec = {
        "version": 1,
        "symbol": "DEMO",
        "rules": [
            {"when": {"type": "compare", "op": ">=", "left": {"type": "bars_held"}, "right": {"type": "number", "value": 5}},
             "then": {"type": "set_position", "percent": 0}},
            {"when": {"type": "not", "arg": {"type": "in_position"}},
             "then": {"type": "set_position", "percent": 100}},
        ],
    }
    strategy = BlockStrategy(spec)
    strategy.on_start()
    start = datetime(2024, 1, 1)

    def bar(i: int) -> Bar:
        return Bar(start + timedelta(days=i), "DEMO", 10.0, 10.0, 10.0, 10.0, 1e6)

    def fill(i: int, side: SignalType) -> None:
        from ghosalquant import Fill
        strategy.on_fill(Fill(time_stamp=bar(i).time_stamp, symbol="DEMO", side=side, quantity=100.0, price=10.0))

    assert strategy.on_market_data(bar(0)).metadata[metadata_keys.TARGET_WEIGHT] == 1.0
    # The engine books a fill before the strategy sees the bar it filled on.
    fill(1, SignalType.BUY)
    assert [strategy.on_market_data(bar(i)) for i in range(1, 5)] == [None] * 4
    exit_signal = strategy.on_market_data(bar(5))
    assert exit_signal.metadata[metadata_keys.TARGET_WEIGHT] == 0.0
    assert exit_signal.signal_type is SignalType.SELL
    fill(6, SignalType.SELL)
    entry = strategy.on_market_data(bar(6))
    assert entry.metadata[metadata_keys.TARGET_WEIGHT] == 1.0

    # An order that never filled is asked for again, still labelled as a buy.
    again = strategy.on_market_data(bar(7))
    assert again.metadata[metadata_keys.TARGET_WEIGHT] == 1.0
    assert again.signal_type is SignalType.BUY


# ----- run_spec ----- #

def test_run_spec_returns_a_json_ready_result_and_is_repeatable():
    frame = _bars()
    spec = {**_trend_spec(), "start": "2015-06-01", "end": "2016-12-30", "initial_capital": 50_000}

    result = run_spec(spec, frame)

    assert json.loads(json.dumps(result)) == result
    assert result["symbol"] == "DEMO"
    assert result["start"] >= "2015-06-01" and result["end"] <= "2016-12-30"
    assert result["bars"] == len(result["equity"])
    assert result["equity"][0]["buy_hold"] == pytest.approx(50_000)
    assert result["trades"] and {"t", "side", "quantity", "price"} == set(result["trades"][0])
    assert result["metrics"]["final_equity"] == pytest.approx(result["equity"][-1]["strategy"])
    assert result["metrics"]["max_drawdown_pct"] >= 0.0
    assert run_spec(spec, frame) == result


def test_run_spec_refuses_data_it_cannot_use():
    frame = _bars(100)

    with pytest.raises(SpecError, match="no price data"):
        run_spec({**_trend_spec(), "start": "2030-01-01"}, frame)
    with pytest.raises(SpecError, match="Volume"):
        run_spec(_trend_spec(), frame.drop(columns=["Volume"]))


def test_bars_from_payload_reads_the_relay_format():
    payload = {
        "t": [1704153600, 1704240000, 1704326400],
        "o": [10.0, None, 12.0], "h": [11.0, 12.0, 13.0], "l": [9.0, 10.0, 11.0],
        "c": [10.5, 11.5, 12.5], "v": [100, 200, None],
    }

    frame = bars_from_payload(payload)

    assert list(frame.index.strftime("%Y-%m-%d")) == ["2024-01-02", "2024-01-04"]
    assert frame["Volume"].tolist() == [100.0, 0.0]
    with pytest.raises(SpecError):
        bars_from_payload({"t": [1]})


def test_the_package_and_blocks_import_without_yfinance():
    # A browser build has no yfinance; the import must not need it.
    code = (
        "import sys; sys.modules['yfinance'] = None\n"
        "import ghosalquant, ghosalquant.blocks, ghosalquant.backtester, ghosalquant.data_layer\n"
        "from ghosalquant.data_layer.yfinance_datasource import YFinanceDataSource\n"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
