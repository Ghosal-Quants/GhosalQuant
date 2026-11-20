from __future__ import annotations

from typing import Any

from ..core import metadata_keys
from ..core.types import Bar, Fill, Signal, SignalType
from ..execution.strategy_base import Strategy
from .nodes import BarContext, Node, compile_node
from .schema import validate_spec

_QTY_EPS = 1e-9
_WEIGHT_EPS = 1e-9


class BlockStrategy(Strategy):
    """
    Runs a block-strategy spec (see ``ghosalquant.blocks.schema``) as an ordinary ``Strategy``.

    Each bar, every rule's condition is evaluated in order and the first true one sets the target
    weight. A signal is sent only when that changes what the strategy wants to hold, so a rule
    that stays true does not rebalance on every bar. Long only.
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        super().__init__()
        self.spec = validate_spec(spec)
        self.symbol: str = self.spec["symbol"]
        self.fills: list[Fill] = []
        self._build()

    def _build(self) -> None:
        self._rules: list[tuple[Node, float]] = [
            (compile_node(rule["when"]), rule["then"]["percent"] / 100.0) for rule in self.spec["rules"]
        ]
        self._target: float | None = None
        self._quantity = 0.0
        self._cost = 0.0
        self._bar_index = 0
        self._opened_at: int | None = None
        self.fills = []

    def on_start(self) -> None:
        # Indicators carry history, so a second run must not start from the first run's state.
        self._build()

    def on_end(self) -> None:
        pass

    def on_market_data(self, bar: Bar) -> Signal | None:
        self._bar_index += 1
        in_position = self._quantity > _QTY_EPS
        entry = self._cost / self._quantity if in_position else None
        ctx = BarContext(
            bar=bar,
            in_position=in_position,
            position_return_pct=(bar.close / entry - 1.0) * 100.0 if entry else None,
            bars_held=self._bar_index - self._opened_at if in_position and self._opened_at is not None else None,
        )

        chosen: float | None = None
        for condition, weight in self._rules:
            # Evaluate every rule, not just up to the first hit, so each indicator sees each bar.
            if condition.step(ctx) and chosen is None:
                chosen = weight

        if chosen is None:
            return None
        # Same target as last time and the position agrees with it: nothing to do.
        if (
            self._target is not None
            and abs(chosen - self._target) <= _WEIGHT_EPS
            and (chosen > _WEIGHT_EPS) == in_position
        ):
            return None

        previous = self._target if self._target is not None else 0.0
        self._target = chosen
        return Signal(
            time_stamp=bar.time_stamp,
            symbol=bar.symbol,
            ref_price=bar.close,
            # The risk manager picks the side from the weight; this only labels the signal.
            signal_type=SignalType.BUY if chosen > previous or (chosen > _WEIGHT_EPS and not in_position) else SignalType.SELL,
            metadata={metadata_keys.TARGET_WEIGHT: chosen},
        )

    def on_fill(self, fill: Fill) -> None:
        self.fills.append(fill)
        if fill.side is SignalType.BUY:
            if self._quantity <= _QTY_EPS:
                self._opened_at = self._bar_index
                self._quantity = 0.0
                self._cost = 0.0
            self._quantity += fill.quantity
            self._cost += fill.quantity * fill.price
        else:
            sold = min(fill.quantity, self._quantity)
            if self._quantity > _QTY_EPS:
                # Selling leaves the average entry price of what remains unchanged.
                self._cost -= self._cost * (sold / self._quantity)
            self._quantity -= sold
            if self._quantity <= _QTY_EPS:
                self._quantity = 0.0
                self._cost = 0.0
                self._opened_at = None
