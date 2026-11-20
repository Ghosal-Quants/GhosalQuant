"""
The evaluation tree a validated spec compiles to.

Every node has ``step(ctx)``, called exactly once per bar, in bar order. A node only ever sees the
current bar and what it has kept from earlier ones, so no node can read the future. ``None``
means "no value yet" (an indicator still warming up, a division by zero, no open position); it
propagates through arithmetic, and any comparison involving it is false.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from ..core.types import Bar


@dataclass(slots=True)
class BarContext:
    """What a node may look at for the current bar."""
    bar: Bar
    in_position: bool
    position_return_pct: float | None
    bars_held: int | None


class Node:
    def step(self, ctx: BarContext) -> Any:
        raise NotImplementedError


# ----- Numbers ----- #

class Const(Node):
    def __init__(self, value: float) -> None:
        self.value = value

    def step(self, ctx: BarContext) -> float:
        return self.value


class Price(Node):
    def __init__(self, field: str) -> None:
        self.field = field

    def step(self, ctx: BarContext) -> float | None:
        value = float(getattr(ctx.bar, self.field))
        return value if math.isfinite(value) else None


class PositionReturn(Node):
    def step(self, ctx: BarContext) -> float | None:
        return ctx.position_return_pct


class BarsHeld(Node):
    def step(self, ctx: BarContext) -> float | None:
        return None if ctx.bars_held is None else float(ctx.bars_held)


_MATH: dict[str, Callable[[float, float], float | None]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": lambda a, b: None if b == 0.0 else a / b,
}


class Math(Node):
    def __init__(self, op: str, left: Node, right: Node) -> None:
        self.fn = _MATH[op]
        self.left = left
        self.right = right

    def step(self, ctx: BarContext) -> float | None:
        a = self.left.step(ctx)
        b = self.right.step(ctx)
        if a is None or b is None:
            return None
        out = self.fn(a, b)
        return out if out is not None and math.isfinite(out) else None


class Previous(Node):
    """The value its child had ``bars`` bars ago."""

    def __init__(self, of: Node, bars: int) -> None:
        self.of = of
        self.history: deque[float | None] = deque(maxlen=bars + 1)

    def step(self, ctx: BarContext) -> float | None:
        self.history.append(self.of.step(ctx))
        if len(self.history) < self.history.maxlen:
            return None
        return self.history[0]


# ----- Indicators ----- #

class _Windowed(Node):
    """An indicator over the last ``period`` values of its source; a missing value restarts it."""

    def __init__(self, source: Node, period: int) -> None:
        self.source = source
        self.period = period
        self.window: deque[float] = deque(maxlen=period)

    def step(self, ctx: BarContext) -> float | None:
        value = self.source.step(ctx)
        if value is None:
            self.window.clear()
            return None
        self.window.append(value)
        if len(self.window) < self.period:
            return None
        return self.compute()

    def compute(self) -> float | None:
        raise NotImplementedError


class Sma(_Windowed):
    def compute(self) -> float:
        return math.fsum(self.window) / self.period


class Highest(_Windowed):
    def compute(self) -> float:
        return max(self.window)


class Lowest(_Windowed):
    def compute(self) -> float:
        return min(self.window)


class StdDev(_Windowed):
    """Population standard deviation, the convention Bollinger bands use."""

    def compute(self) -> float:
        mean = math.fsum(self.window) / self.period
        return math.sqrt(math.fsum((x - mean) ** 2 for x in self.window) / self.period)


class Bollinger(_Windowed):
    def __init__(self, source: Node, period: int, mult: float, sign: float) -> None:
        super().__init__(source, period)
        self.offset = mult * sign

    def compute(self) -> float:
        mean = math.fsum(self.window) / self.period
        std = math.sqrt(math.fsum((x - mean) ** 2 for x in self.window) / self.period)
        return mean + self.offset * std


class Roc(Node):
    """Percent change against the value ``period`` bars ago."""

    def __init__(self, source: Node, period: int) -> None:
        self.source = source
        self.window: deque[float] = deque(maxlen=period + 1)

    def step(self, ctx: BarContext) -> float | None:
        value = self.source.step(ctx)
        if value is None:
            self.window.clear()
            return None
        self.window.append(value)
        if len(self.window) < self.window.maxlen or self.window[0] == 0.0:
            return None
        return (value / self.window[0] - 1.0) * 100.0


class Ema(Node):
    """Exponential average with alpha 2 / (period + 1), seeded with the SMA of the first ``period`` values."""

    def __init__(self, source: Node, period: int) -> None:
        self.source = source
        self.period = period
        self.alpha = 2.0 / (period + 1.0)
        self.seed: list[float] = []
        self.value: float | None = None

    def step(self, ctx: BarContext) -> float | None:
        x = self.source.step(ctx)
        if x is None:
            self.seed.clear()
            self.value = None
            return None
        if self.value is None:
            self.seed.append(x)
            if len(self.seed) < self.period:
                return None
            self.value = math.fsum(self.seed) / self.period
            return self.value
        self.value += self.alpha * (x - self.value)
        return self.value


class _Wilder:
    """Wilder's smoothing: the mean of the first ``period`` values, then a 1/period exponential."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.count = 0
        self.total = 0.0
        self.value: float | None = None

    def push(self, x: float) -> float | None:
        if self.value is None:
            self.count += 1
            self.total += x
            if self.count == self.period:
                self.value = self.total / self.period
            return self.value
        self.value += (x - self.value) / self.period
        return self.value

    def reset(self) -> None:
        self.count = 0
        self.total = 0.0
        self.value = None


class Rsi(Node):
    def __init__(self, source: Node, period: int) -> None:
        self.source = source
        self.gain = _Wilder(period)
        self.loss = _Wilder(period)
        self.last: float | None = None

    def step(self, ctx: BarContext) -> float | None:
        x = self.source.step(ctx)
        if x is None:
            self.last = None
            self.gain.reset()
            self.loss.reset()
            return None
        if self.last is None:
            self.last = x
            return None
        change = x - self.last
        self.last = x
        gain = self.gain.push(max(change, 0.0))
        loss = self.loss.push(max(-change, 0.0))
        if gain is None or loss is None:
            return None
        if loss == 0.0:
            return 100.0 if gain > 0.0 else 50.0
        return 100.0 - 100.0 / (1.0 + gain / loss)


class Atr(Node):
    def __init__(self, period: int) -> None:
        self.smooth = _Wilder(period)
        self.last_close: float | None = None

    def step(self, ctx: BarContext) -> float | None:
        bar = ctx.bar
        if self.last_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(bar.high - bar.low, abs(bar.high - self.last_close), abs(bar.low - self.last_close))
        self.last_close = bar.close
        return self.smooth.push(true_range)


# ----- Conditions ----- #

_COMPARE: dict[str, Callable[[float, float], bool]] = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
}


class Compare(Node):
    def __init__(self, op: str, left: Node, right: Node) -> None:
        self.fn = _COMPARE[op]
        self.left = left
        self.right = right

    def step(self, ctx: BarContext) -> bool:
        a = self.left.step(ctx)
        b = self.right.step(ctx)
        return a is not None and b is not None and self.fn(a, b)


class Cross(Node):
    """True on the bar where ``left`` moves from at-or-below ``right`` to above it (or the reverse)."""

    def __init__(self, direction: str, left: Node, right: Node) -> None:
        self.sign = 1.0 if direction == "above" else -1.0
        self.left = left
        self.right = right
        self.last_diff: float | None = None

    def step(self, ctx: BarContext) -> bool:
        a = self.left.step(ctx)
        b = self.right.step(ctx)
        if a is None or b is None:
            self.last_diff = None
            return False
        diff = (a - b) * self.sign
        crossed = self.last_diff is not None and self.last_diff <= 0.0 < diff
        self.last_diff = diff
        return crossed


class And(Node):
    def __init__(self, args: list[Node]) -> None:
        self.args = args

    def step(self, ctx: BarContext) -> bool:
        # No short-circuit: every child must see every bar to keep its history.
        return all([arg.step(ctx) for arg in self.args])


class Or(Node):
    def __init__(self, args: list[Node]) -> None:
        self.args = args

    def step(self, ctx: BarContext) -> bool:
        return any([arg.step(ctx) for arg in self.args])


class Not(Node):
    def __init__(self, arg: Node) -> None:
        self.arg = arg

    def step(self, ctx: BarContext) -> bool:
        return not self.arg.step(ctx)


class InPosition(Node):
    def step(self, ctx: BarContext) -> bool:
        return ctx.in_position


# ----- Compilation ----- #

def compile_node(node: dict[str, Any]) -> Node:
    """Build the evaluation tree for one node of a spec ``validate_spec`` has already accepted."""
    kind = node["type"]

    if kind == "number":
        return Const(node["value"])
    if kind == "price":
        return Price(node["field"])
    if kind == "position_return":
        return PositionReturn()
    if kind == "bars_held":
        return BarsHeld()
    if kind == "math":
        return Math(node["op"], compile_node(node["left"]), compile_node(node["right"]))
    if kind == "previous":
        return Previous(compile_node(node["of"]), node["bars"])
    if kind == "indicator":
        return _compile_indicator(node)

    if kind == "compare":
        return Compare(node["op"], compile_node(node["left"]), compile_node(node["right"]))
    if kind == "cross":
        return Cross(node["direction"], compile_node(node["left"]), compile_node(node["right"]))
    if kind == "and":
        return And([compile_node(arg) for arg in node["args"]])
    if kind == "or":
        return Or([compile_node(arg) for arg in node["args"]])
    if kind == "not":
        return Not(compile_node(node["arg"]))
    if kind == "in_position":
        return InPosition()

    raise ValueError(f"Unknown node type {kind!r}; validate the spec first")


def _compile_indicator(node: dict[str, Any]) -> Node:
    name = node["name"]
    period = node["period"]
    if name == "atr":
        return Atr(period)

    source = compile_node(node["source"])
    if name == "sma":
        return Sma(source, period)
    if name == "ema":
        return Ema(source, period)
    if name == "rsi":
        return Rsi(source, period)
    if name == "highest":
        return Highest(source, period)
    if name == "lowest":
        return Lowest(source, period)
    if name == "stddev":
        return StdDev(source, period)
    if name == "roc":
        return Roc(source, period)
    if name == "bollinger_upper":
        return Bollinger(source, period, node["mult"], 1.0)
    if name == "bollinger_lower":
        return Bollinger(source, period, node["mult"], -1.0)

    raise ValueError(f"Unknown indicator {name!r}; validate the spec first")
