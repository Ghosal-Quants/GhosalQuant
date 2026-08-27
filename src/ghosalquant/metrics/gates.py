from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import math

COMPARATORS = {
    ">=": lambda actual, threshold: actual >= threshold,
    ">": lambda actual, threshold: actual > threshold,
    "<=": lambda actual, threshold: actual <= threshold,
    "<": lambda actual, threshold: actual < threshold,
}


@dataclass(frozen=True, slots=True)
class MetricRef:
    """Names of metrics to read, in order of preference: the first one present wins."""
    keys: tuple[str, ...]

    def __init__(self, *keys: str) -> None:
        if not keys:
            raise ValueError("MetricRef needs at least one key")
        object.__setattr__(self, "keys", tuple(keys))


@dataclass(frozen=True, slots=True)
class GateSpec:
    """
    One go/no-go condition on a metrics mapping.

    ``actual`` and ``threshold`` are either literals or ``MetricRef``s. ``offset`` is added to the
    resolved threshold, so a gate can require a margin over a benchmark, and ``magnitude``
    compares absolute values, which is how drawdowns are compared.
    """
    name: str
    actual: "MetricRef | float"
    comparator: str
    threshold: "MetricRef | float"
    offset: float = 0.0
    magnitude: bool = False
    message: str = ""

    def __post_init__(self) -> None:
        if self.comparator not in COMPARATORS:
            raise ValueError(f"comparator must be one of {sorted(COMPARATORS)}")


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    passed: bool
    actual: float
    threshold: float
    comparator: str
    message: str


@dataclass(frozen=True, slots=True)
class GateEvaluation:
    checks: tuple[GateCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> tuple[GateCheck, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def __str__(self) -> str:
        if not self.checks:
            return "No gates evaluated."
        lines = [f"{'PASS' if self.passed else 'FAIL'} ({len(self.failures)}/{len(self.checks)} failing)"]
        for check in self.checks:
            lines.append(
                f"  [{'ok' if check.passed else 'XX'}] {check.name}: "
                f"{check.actual:.4f} {check.comparator} {check.threshold:.4f}"
                + (f" — {check.message}" if check.message and not check.passed else "")
            )
        return "\n".join(lines)


def evaluate_gates(metrics: Mapping[str, Any], spec: Sequence[GateSpec]) -> GateEvaluation:
    """Evaluate every gate in ``spec`` against ``metrics``."""
    checks: list[GateCheck] = []
    for gate in spec:
        actual = _resolve(metrics, gate.actual)
        threshold = _resolve(metrics, gate.threshold)
        if gate.magnitude:
            actual, threshold = abs(actual), abs(threshold)
        threshold += gate.offset
        checks.append(
            GateCheck(
                name=gate.name,
                passed=COMPARATORS[gate.comparator](actual, threshold),
                actual=actual,
                threshold=threshold,
                comparator=gate.comparator,
                message=gate.message,
            )
        )
    return GateEvaluation(checks=tuple(checks))


def winner_concentration_shares(metrics: Mapping[str, Any]) -> tuple[float, float]:
    """
    Share of positive realised PnL contributed by the best, and the best two, closed trades.

    Reads precomputed shares when present, otherwise derives them from a list of closed-trade
    PnLs. Returns infinities when nothing was won, so a concentration gate fails rather than
    passing vacuously.
    """
    largest = _maybe(metrics, "largest_winner_realised_pnl_share", "largest_winner_pnl_share")
    top_two = _maybe(metrics, "top_two_winner_realised_pnl_share", "top_two_winner_pnl_share")
    if largest is not None and top_two is not None:
        return largest, top_two

    raw = metrics.get("closed_trade_pnls", metrics.get("realised_pnls", ()))
    pnls = sorted((float(value) for value in raw if float(value) > 0.0), reverse=True)
    total = sum(pnls)
    if total <= 0.0:
        return math.inf, math.inf
    return (
        largest if largest is not None else pnls[0] / total,
        top_two if top_two is not None else sum(pnls[:2]) / total,
    )


def with_winner_concentration(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """``metrics`` plus the derived winner-concentration shares, ready for a concentration gate."""
    largest, top_two = winner_concentration_shares(metrics)
    return {
        **metrics,
        "largest_winner_realised_pnl_share": largest,
        "top_two_winner_realised_pnl_share": top_two,
    }


def _resolve(metrics: Mapping[str, Any], value: "MetricRef | float") -> float:
    if isinstance(value, MetricRef):
        resolved = _maybe(metrics, *value.keys)
        if resolved is None:
            raise KeyError(f"missing metric; expected one of {value.keys}")
        return resolved
    return float(value)


def _maybe(metrics: Mapping[str, Any], *names: str) -> float | None:
    for name in names:
        if name in metrics and metrics[name] is not None:
            return float(metrics[name])
    return None
