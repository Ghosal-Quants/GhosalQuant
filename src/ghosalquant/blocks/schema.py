"""
The block-strategy spec: a JSON document a visual editor produces, and its validation.

A spec is data, never code. ``validate_spec`` checks every node and returns a normalised copy,
or raises ``SpecError`` naming the path of the first thing wrong with it::

    {
      "version": 1,
      "symbol": "AAPL",
      "start": "2015-01-01",          # optional
      "end": "2025-01-01",            # optional
      "initial_capital": 10000,       # optional
      "costs": {"spread_bps": 2, "commission_per_share": 0},   # optional
      "rules": [
        {"when": <condition>, "then": {"type": "set_position", "percent": 100}}
      ]
    }

Each bar, every rule's condition is evaluated in order and the first one that is true sets the
share of equity to hold in the symbol.

Number nodes::

    {"type": "number", "value": 50}
    {"type": "price", "field": "close"}                      # open, high, low, close, volume
    {"type": "indicator", "name": "sma", "period": 50, "source": <number>}
    {"type": "math", "op": "+", "left": <number>, "right": <number>}     # + - * /
    {"type": "previous", "of": <number>, "bars": 1}          # the value that many bars ago
    {"type": "position_return"}                              # percent gain on the open position
    {"type": "bars_held"}                                    # bars since the position opened

Indicators: ``sma``, ``ema``, ``rsi``, ``highest``, ``lowest``, ``stddev``, ``roc`` (percent
change over ``period`` bars), ``atr`` (no source), and ``bollinger_upper`` / ``bollinger_lower``
(with ``mult``, default 2). ``source`` defaults to the close.

Condition nodes::

    {"type": "compare", "op": ">", "left": <number>, "right": <number>}  # > < >= <=
    {"type": "cross", "direction": "above", "left": <number>, "right": <number>}
    {"type": "and", "args": [<condition>, ...]}
    {"type": "or", "args": [<condition>, ...]}
    {"type": "not", "arg": <condition>}
    {"type": "in_position"}
"""

from __future__ import annotations

import math
import re
from datetime import date
from typing import Any

SPEC_VERSION = 1

MAX_RULES = 20
MAX_DEPTH = 12
MAX_NODES = 200
MAX_PERIOD = 500
MAX_LOGIC_ARGS = 8

PRICE_FIELDS = ("open", "high", "low", "close", "volume")
INDICATORS = (
    "sma", "ema", "rsi", "highest", "lowest", "stddev", "roc", "atr",
    "bollinger_upper", "bollinger_lower",
)
MATH_OPS = ("+", "-", "*", "/")
COMPARE_OPS = (">", "<", ">=", "<=")
CROSS_DIRECTIONS = ("above", "below")

_SYMBOL = re.compile(r"^[A-Z0-9^][A-Z0-9.\-^=]{0,14}$")


class SpecError(ValueError):
    """A spec that cannot be run. ``path`` locates the offending node, e.g. ``rules[0].when.left``."""

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}" if path else message)
        self.path = path
        self.message = message


class _Budget:
    """Counts nodes across the whole spec so a huge document is refused before it is compiled."""

    def __init__(self) -> None:
        self.nodes = 0

    def spend(self, path: str) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise SpecError(path, f"the strategy has more than {MAX_NODES} blocks")


def validate_spec(spec: Any) -> dict[str, Any]:
    """Check ``spec`` and return a normalised copy with every default filled in."""
    if not isinstance(spec, dict):
        raise SpecError("", "the strategy must be an object")
    _only_keys(spec, "", {"version", "name", "symbol", "start", "end", "initial_capital", "costs", "rules"})

    if spec.get("version") != SPEC_VERSION:
        raise SpecError("version", f"must be {SPEC_VERSION}")

    symbol = spec.get("symbol")
    if not isinstance(symbol, str) or not _SYMBOL.match(symbol.strip().upper()):
        raise SpecError("symbol", "must be a ticker such as AAPL")

    start = _date(spec.get("start"), "start")
    end = _date(spec.get("end"), "end")
    if start and end and start >= end:
        raise SpecError("end", "must be after start")

    capital = spec.get("initial_capital", 10_000.0)
    capital = _number(capital, "initial_capital", minimum=100.0, maximum=1e9)

    costs_in = spec.get("costs") or {}
    if not isinstance(costs_in, dict):
        raise SpecError("costs", "must be an object")
    _only_keys(costs_in, "costs", {"spread_bps", "commission_per_share"})
    costs = {
        "spread_bps": _number(costs_in.get("spread_bps", 2.0), "costs.spread_bps", minimum=0.0, maximum=500.0),
        "commission_per_share": _number(
            costs_in.get("commission_per_share", 0.0), "costs.commission_per_share", minimum=0.0, maximum=10.0,
        ),
    }

    rules_in = spec.get("rules")
    if not isinstance(rules_in, list) or not rules_in:
        raise SpecError("rules", "add at least one rule")
    if len(rules_in) > MAX_RULES:
        raise SpecError("rules", f"at most {MAX_RULES} rules")

    budget = _Budget()
    rules = []
    for i, rule in enumerate(rules_in):
        path = f"rules[{i}]"
        if not isinstance(rule, dict):
            raise SpecError(path, "must be an object")
        _only_keys(rule, path, {"when", "then"})
        rules.append({
            "when": _condition(rule.get("when"), f"{path}.when", 1, budget),
            "then": _action(rule.get("then"), f"{path}.then"),
        })

    name = spec.get("name")
    if name is not None and (not isinstance(name, str) or len(name) > 80):
        raise SpecError("name", "must be text of at most 80 characters")

    return {
        "version": SPEC_VERSION,
        "name": name,
        "symbol": symbol.strip().upper(),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "initial_capital": capital,
        "costs": costs,
        "rules": rules,
    }


# ----- Nodes ----- #

def _action(node: Any, path: str) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise SpecError(path, "this rule has no action")
    _only_keys(node, path, {"type", "percent"})
    if node.get("type") != "set_position":
        raise SpecError(f"{path}.type", "must be set_position")
    percent = _number(node.get("percent"), f"{path}.percent", minimum=0.0, maximum=100.0)
    return {"type": "set_position", "percent": percent}


def _condition(node: Any, path: str, depth: int, budget: _Budget) -> dict[str, Any]:
    kind = _enter(node, path, depth, budget, "this rule has no condition")

    if kind == "compare":
        _only_keys(node, path, {"type", "op", "left", "right"})
        return {
            "type": "compare",
            "op": _choice(node.get("op"), f"{path}.op", COMPARE_OPS),
            "left": _numeric(node.get("left"), f"{path}.left", depth + 1, budget),
            "right": _numeric(node.get("right"), f"{path}.right", depth + 1, budget),
        }
    if kind == "cross":
        _only_keys(node, path, {"type", "direction", "left", "right"})
        return {
            "type": "cross",
            "direction": _choice(node.get("direction"), f"{path}.direction", CROSS_DIRECTIONS),
            "left": _numeric(node.get("left"), f"{path}.left", depth + 1, budget),
            "right": _numeric(node.get("right"), f"{path}.right", depth + 1, budget),
        }
    if kind in ("and", "or"):
        _only_keys(node, path, {"type", "args"})
        args = node.get("args")
        if not isinstance(args, list) or len(args) < 2:
            raise SpecError(f"{path}.args", "needs at least two conditions")
        if len(args) > MAX_LOGIC_ARGS:
            raise SpecError(f"{path}.args", f"at most {MAX_LOGIC_ARGS} conditions")
        return {
            "type": kind,
            "args": [_condition(arg, f"{path}.args[{i}]", depth + 1, budget) for i, arg in enumerate(args)],
        }
    if kind == "not":
        _only_keys(node, path, {"type", "arg"})
        return {"type": "not", "arg": _condition(node.get("arg"), f"{path}.arg", depth + 1, budget)}
    if kind == "in_position":
        _only_keys(node, path, {"type"})
        return {"type": "in_position"}

    raise SpecError(f"{path}.type", f"{kind!r} is not a condition")


def _numeric(node: Any, path: str, depth: int, budget: _Budget) -> dict[str, Any]:
    kind = _enter(node, path, depth, budget, "a value is missing here")

    if kind == "number":
        _only_keys(node, path, {"type", "value"})
        return {"type": "number", "value": _number(node.get("value"), f"{path}.value", minimum=-1e12, maximum=1e12)}
    if kind == "price":
        _only_keys(node, path, {"type", "field"})
        return {"type": "price", "field": _choice(node.get("field", "close"), f"{path}.field", PRICE_FIELDS)}
    if kind == "indicator":
        _only_keys(node, path, {"type", "name", "period", "source", "mult"})
        name = _choice(node.get("name"), f"{path}.name", INDICATORS)
        out: dict[str, Any] = {
            "type": "indicator",
            "name": name,
            "period": _period(node.get("period"), f"{path}.period"),
        }
        if name == "atr":
            if node.get("source") is not None:
                raise SpecError(f"{path}.source", "atr takes no source")
        else:
            source = node.get("source", {"type": "price", "field": "close"})
            out["source"] = _numeric(source, f"{path}.source", depth + 1, budget)
        if name in ("bollinger_upper", "bollinger_lower"):
            out["mult"] = _number(node.get("mult", 2.0), f"{path}.mult", minimum=0.1, maximum=10.0)
        elif node.get("mult") is not None:
            raise SpecError(f"{path}.mult", f"{name} takes no mult")
        return out
    if kind == "math":
        _only_keys(node, path, {"type", "op", "left", "right"})
        return {
            "type": "math",
            "op": _choice(node.get("op"), f"{path}.op", MATH_OPS),
            "left": _numeric(node.get("left"), f"{path}.left", depth + 1, budget),
            "right": _numeric(node.get("right"), f"{path}.right", depth + 1, budget),
        }
    if kind == "previous":
        _only_keys(node, path, {"type", "of", "bars"})
        return {
            "type": "previous",
            "of": _numeric(node.get("of"), f"{path}.of", depth + 1, budget),
            "bars": _period(node.get("bars", 1), f"{path}.bars"),
        }
    if kind in ("position_return", "bars_held"):
        _only_keys(node, path, {"type"})
        return {"type": kind}

    raise SpecError(f"{path}.type", f"{kind!r} is not a value")


# ----- Helpers ----- #

def _enter(node: Any, path: str, depth: int, budget: _Budget, missing: str) -> str:
    if node is None:
        raise SpecError(path, missing)
    if not isinstance(node, dict):
        raise SpecError(path, "must be an object")
    if depth > MAX_DEPTH:
        raise SpecError(path, f"blocks are nested more than {MAX_DEPTH} deep")
    budget.spend(path)
    kind = node.get("type")
    if not isinstance(kind, str):
        raise SpecError(f"{path}.type", "is missing")
    return kind


def _only_keys(node: dict, path: str, allowed: set[str]) -> None:
    for key in node:
        if key not in allowed:
            raise SpecError(f"{path}.{key}" if path else str(key), "is not a known field")


def _choice(value: Any, path: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise SpecError(path, f"must be one of {', '.join(allowed)}")
    return value


def _number(value: Any, path: str, *, minimum: float, maximum: float) -> float:
    # bool is an int in Python; a true/false here is a mistake, not a number.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SpecError(path, "must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise SpecError(path, f"must be between {minimum:g} and {maximum:g}")
    return number


def _period(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != int(value):
        raise SpecError(path, "must be a whole number")
    period = int(value)
    if period < 1 or period > MAX_PERIOD:
        raise SpecError(path, f"must be between 1 and {MAX_PERIOD}")
    return period


def _date(value: Any, path: str) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise SpecError(path, "must be a date such as 2020-01-31")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SpecError(path, "must be a date such as 2020-01-31") from None
