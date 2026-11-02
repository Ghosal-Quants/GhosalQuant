import math
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from ghosalquant.core.types import EquityPoint
from ghosalquant.metrics import (
    GateEvaluation,
    GateSpec,
    MetricRef,
    evaluate_gates,
    format_metric,
    max_drawdown,
    risk_adjusted_metrics,
    winner_concentration_shares,
    with_winner_concentration,
)
from ghosalquant.metrics.risk_adjusted import SECONDS_PER_YEAR

START = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _curve(values, *, step: timedelta = timedelta(days=1)):
    return [
        EquityPoint(time_stamp=START + step * i, equity=float(value), realised_pnl=0.0,
                    unrealised_pnl=0.0, num_open_trades=0)
        for i, value in enumerate(values)
    ]


def _frame(points):
    return pd.DataFrame([{"time_stamp": p.time_stamp, "equity": p.equity} for p in points])


# ----- max_drawdown ----- #

def test_max_drawdown():
    assert max_drawdown([]) == 0.0
    assert max_drawdown([100.0, 110.0, 120.0]) == 0.0
    assert max_drawdown([100.0, 80.0]) == pytest.approx(-0.2)
    assert max_drawdown([100.0, 120.0, 60.0, 90.0]) == pytest.approx(-0.5)
    assert max_drawdown([100.0, float("nan"), 50.0]) == pytest.approx(-0.5)
    assert max_drawdown([-5.0, -10.0]) == 0.0


def test_format_metric():
    assert format_metric(None) == "n/a"
    assert format_metric(float("nan")) == "n/a"
    assert format_metric(float("inf")) == "n/a"
    assert format_metric("abc") == "n/a"
    assert format_metric(1.239) == "1.24"
    assert format_metric(1.239, ".3f") == "1.239"


# ----- risk_adjusted_metrics ----- #

def test_empty_and_single_point_curves_are_safe():
    for curve in ([], _curve([100.0])):
        metrics = risk_adjusted_metrics(curve, init_capital=100.0)
        assert metrics["return_observations"] == 0
        assert metrics["periods_per_year"] is None
        assert metrics["sharpe_ratio"] is None
        assert metrics["sortino_ratio"] is None
        assert metrics["calmar_ratio"] is None
        assert metrics["annualized_return_pct"] is None
        assert metrics["max_drawdown"] == 0.0
        assert metrics["elapsed_days"] == 0.0


def test_periods_per_year_is_inferred_from_elapsed_time():
    daily = risk_adjusted_metrics(_curve([100.0] * 11), init_capital=100.0)
    assert daily["return_observations"] == 10
    assert daily["elapsed_days"] == pytest.approx(10.0)
    assert daily["periods_per_year"] == pytest.approx(10 / (10 * 86_400 / SECONDS_PER_YEAR))

    hourly = risk_adjusted_metrics(_curve([100.0] * 11, step=timedelta(hours=1)), init_capital=100.0)
    assert hourly["periods_per_year"] > daily["periods_per_year"]


def test_flat_curve_has_no_volatility_and_no_ratios():
    metrics = risk_adjusted_metrics(_curve([100.0] * 5), init_capital=100.0)
    assert metrics["return_volatility_pct"] is None
    assert metrics["downside_volatility_pct"] is None
    assert metrics["sharpe_ratio"] is None
    assert metrics["sortino_ratio"] is None
    assert metrics["annualized_return_pct"] == pytest.approx(0.0)


def test_annualised_return_and_calmar():
    curve = _curve([100.0, 120.0, 90.0, 150.0])
    metrics = risk_adjusted_metrics(curve, init_capital=100.0)

    elapsed_years = 3 * 86_400 / SECONDS_PER_YEAR
    expected = math.exp(math.log(150.0 / 100.0) / elapsed_years) - 1.0
    assert metrics["annualized_return_pct"] == pytest.approx(expected * 100.0)
    assert metrics["max_drawdown"] == pytest.approx(-0.25)
    assert metrics["calmar_ratio"] == pytest.approx(expected / 0.25)


def test_rising_curve_has_positive_sharpe_and_no_downside():
    metrics = risk_adjusted_metrics(_curve([100.0, 101.0, 102.5, 103.0, 105.0]), init_capital=100.0)
    assert metrics["sharpe_ratio"] > 0.0
    assert metrics["sortino_ratio"] is None  # never a losing period
    assert metrics["downside_volatility_pct"] is None
    assert metrics["max_drawdown"] == 0.0


def test_risk_free_rate_lowers_sharpe():
    curve = _curve([100.0, 101.0, 100.5, 102.0, 103.0])
    base = risk_adjusted_metrics(curve, init_capital=100.0)
    charged = risk_adjusted_metrics(curve, init_capital=100.0, risk_free_rate=0.05)
    assert charged["sharpe_ratio"] < base["sharpe_ratio"]
    assert charged["return_volatility_pct"] == pytest.approx(base["return_volatility_pct"])


def test_dataframe_and_equity_points_agree():
    points = _curve([100.0, 104.0, 98.0, 107.0, 101.0])
    from_points = risk_adjusted_metrics(points, init_capital=100.0)
    from_frame = risk_adjusted_metrics(_frame(points), init_capital=100.0)
    assert from_points == from_frame


def test_dataframe_is_sorted_and_coerced():
    frame = _frame(_curve([100.0, 90.0, 120.0]))
    shuffled = frame.iloc[::-1].reset_index(drop=True)
    assert risk_adjusted_metrics(shuffled, init_capital=100.0) == risk_adjusted_metrics(frame, init_capital=100.0)

    with_junk = pd.concat([frame, pd.DataFrame([{"time_stamp": START, "equity": None}])], ignore_index=True)
    assert risk_adjusted_metrics(with_junk, init_capital=100.0)["return_observations"] == 2


def test_custom_column_names():
    frame = pd.DataFrame([{"ts": START + timedelta(days=i), "nav": v} for i, v in enumerate([100.0, 110.0])])
    metrics = risk_adjusted_metrics(frame, init_capital=100.0, timestamp_column="ts", equity_column="nav")
    assert metrics["return_observations"] == 1


def test_zero_equity_period_is_skipped():
    metrics = risk_adjusted_metrics(_curve([100.0, 0.0, 50.0]), init_capital=100.0)
    assert metrics["return_observations"] == 1
    assert metrics["max_drawdown"] == pytest.approx(-1.0)


# ----- gates ----- #

def test_metric_ref_resolves_the_first_present_key():
    metrics = {"return_pct": 12.0}
    spec = [GateSpec("return", MetricRef("strategy_return_pct", "return_pct"), ">=", 10.0)]
    evaluation = evaluate_gates(metrics, spec)
    assert evaluation.passed
    assert evaluation.checks[0].actual == 12.0
    assert evaluation.checks[0].threshold == 10.0


def test_gate_against_another_metric_with_a_margin():
    metrics = {"return_pct": 12.0, "benchmark_return_pct": 10.0}
    spec = [
        GateSpec("beats_bnh", MetricRef("return_pct"), ">=", MetricRef("benchmark_return_pct"),
                 offset=1.0, message="must beat B&H by a point"),
        GateSpec("beats_bnh_by_five", MetricRef("return_pct"), ">=", MetricRef("benchmark_return_pct"), offset=5.0),
    ]
    evaluation = evaluate_gates(metrics, spec)
    assert [check.passed for check in evaluation.checks] == [True, False]
    assert evaluation.checks[1].threshold == 15.0
    assert not evaluation.passed
    assert [check.name for check in evaluation.failures] == ["beats_bnh_by_five"]


def test_magnitude_compares_absolute_values():
    metrics = {"max_drawdown": -0.22, "benchmark_max_drawdown": -0.20}
    tight = GateSpec("dd", MetricRef("max_drawdown"), "<=", MetricRef("benchmark_max_drawdown"),
                     offset=0.01, magnitude=True)
    loose = GateSpec("dd", MetricRef("max_drawdown"), "<=", MetricRef("benchmark_max_drawdown"),
                     offset=0.05, magnitude=True)
    assert not evaluate_gates(metrics, [tight]).passed
    assert evaluate_gates(metrics, [loose]).passed
    assert evaluate_gates(metrics, [tight]).checks[0].actual == pytest.approx(0.22)


def test_all_comparators():
    metrics = {"x": 5.0}
    for comparator, threshold, expected in ((">", 4.0, True), (">", 5.0, False), (">=", 5.0, True),
                                            ("<", 6.0, True), ("<=", 5.0, True), ("<=", 4.0, False)):
        spec = [GateSpec("x", MetricRef("x"), comparator, threshold)]
        assert evaluate_gates(metrics, spec).passed is expected


def test_unknown_comparator_and_missing_metric_are_errors():
    with pytest.raises(ValueError):
        GateSpec("x", MetricRef("x"), "!=", 1.0)
    with pytest.raises(ValueError):
        MetricRef()
    with pytest.raises(KeyError):
        evaluate_gates({}, [GateSpec("x", MetricRef("missing"), ">=", 1.0)])


def test_literal_actual_is_allowed():
    assert evaluate_gates({}, [GateSpec("const", 3.0, ">=", 1.0)]).passed


def test_evaluation_reporting():
    metrics = {"a": 1.0, "b": 5.0}
    evaluation = evaluate_gates(metrics, [
        GateSpec("passes", MetricRef("b"), ">=", 1.0),
        GateSpec("fails", MetricRef("a"), ">=", 2.0, message="too small"),
    ])
    text = str(evaluation)
    assert isinstance(evaluation, GateEvaluation)
    assert "1/2 failing" in text
    assert "too small" in text
    assert str(evaluate_gates({}, [])) == "No gates evaluated."


def test_winner_concentration_shares():
    derived = winner_concentration_shares({"closed_trade_pnls": [10.0, 5.0, 4.0, 1.0, -20.0]})
    assert derived[0] == pytest.approx(0.5)
    assert derived[1] == pytest.approx(0.75)

    assert winner_concentration_shares({"closed_trade_pnls": [-1.0, -2.0]}) == (math.inf, math.inf)
    assert winner_concentration_shares({}) == (math.inf, math.inf)

    precomputed = {"largest_winner_realised_pnl_share": 0.3, "top_two_winner_realised_pnl_share": 0.4}
    assert winner_concentration_shares(precomputed) == (0.3, 0.4)

    assert winner_concentration_shares({"realised_pnls": [3.0, 1.0]})[0] == pytest.approx(0.75)


def test_with_winner_concentration_feeds_a_gate():
    metrics = with_winner_concentration({"closed_trade_pnls": [10.0, 1.0]})
    spec = [GateSpec("concentration", MetricRef("largest_winner_realised_pnl_share"), "<=", 0.5,
                     message="one trade dominates")]
    evaluation = evaluate_gates(metrics, spec)
    assert not evaluation.passed
    assert evaluation.checks[0].actual == pytest.approx(10.0 / 11.0)
