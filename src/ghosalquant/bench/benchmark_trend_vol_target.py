from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from ..core.types import Bar, Signal, SignalType
from ..execution.strategy_base import Strategy

EPS = 1e-4


@dataclass
class EMA:
    span: int
    value: Optional[float] = None

    def update(self, x: float) -> float:
        a = 2.0 / (self.span + 1.0)
        self.value = x if self.value is None else (1.0 - a) * self.value + a * x
        return self.value


@dataclass
class EWStd:
    span: int
    mean: float = 0.0
    var: float = 0.0
    init: bool = False

    def update(self, x: float) -> float:
        a = 2.0 / (self.span + 1.0)
        if not self.init:
            self.mean = x
            self.var = 0.0
            self.init = True
            return 0.0

        prev_mean = self.mean
        self.mean = (1.0 - a) * self.mean + a * x
        self.var = (1.0 - a) * self.var + a * (x - prev_mean) * (x - prev_mean)
        return math.sqrt(max(self.var, 0.0))


class BenchmarkTrendVolTarget(Strategy):
    """
    Long-only single-symbol trend benchmark with volatility targeting.

    Emits a ``target_weight`` instead of a quantity; RiskManager sizes the order from equity.
    """

    def __init__(self) -> None:
        super().__init__()

        self.ema20 = EMA(20)
        self.ema80 = EMA(80)
        self.ema200 = EMA(200)
        self.vol = EWStd(30)
        self.vol_hist: Deque[float] = deque(maxlen=252)

        self._symbol: str | None = None
        self.prev_close: Optional[float] = None
        self.vol_blocked: bool = False
        self.target_weight: float = 0.0

        self.TARGET_VOL = 0.010
        self.MAX_POS = 1.0
        self.VOL_FLOOR = 0.006

        self.ENTER_TREND = 0.0025
        self.EXIT_TREND = 0.0006

        self.WEAK_TREND = 0.0030
        self.STRONG_TREND = 0.0060
        self.WEAK_SCALE = 0.25
        self.MID_SCALE = 0.60

        self.SMOOTH_ALPHA = 0.25

        self.VOL_EXIT_MULT = 2.1
        self.VOL_ENTER_MULT = 1.9

        self.SL_PCT = None
        self.TP_PCT = None

    def on_start(self) -> None:
        return None

    def on_end(self) -> None:
        return None

    def _validate_symbol(self, symbol: str) -> None:
        if self._symbol is None:
            self._symbol = symbol
            return

        if symbol != self._symbol:
            raise ValueError(
                "BenchmarkTrendVolTarget is single-symbol only. "
                f"Received {symbol!r} after {self._symbol!r}."
            )

    def on_market_data(self, bar: Bar) -> Optional[Signal]:
        self._validate_symbol(bar.symbol)
        px = float(bar.close)

        if self.prev_close is None:
            self.prev_close = px
            self.ema20.update(px)
            self.ema80.update(px)
            self.ema200.update(px)
            return None

        r = (px / self.prev_close) - 1.0
        self.prev_close = px

        e20 = self.ema20.update(px)
        e80 = self.ema80.update(px)
        e200 = self.ema200.update(px)

        trend = (e20 - e80) / px if px != 0.0 else 0.0

        v = self.vol.update(r)
        if v > 0.0 and not math.isnan(v):
            self.vol_hist.append(v)

        v_med = None
        if len(self.vol_hist) >= 60:
            vs = sorted(self.vol_hist)
            v_med = vs[len(vs) // 2]

        raw_target = 0.0
        if v_med is not None and v > 0.0 and not any(map(math.isnan, [px, trend, v, v_med, e200])):
            exit_th = v_med * self.VOL_EXIT_MULT
            enter_th = v_med * self.VOL_ENTER_MULT

            if (not self.vol_blocked) and v > exit_th:
                self.vol_blocked = True
            elif self.vol_blocked and v < enter_th:
                self.vol_blocked = False

            trend_gate = self.EXIT_TREND if self.target_weight > EPS else self.ENTER_TREND
            if (not self.vol_blocked) and (px >= e200) and (trend >= trend_gate):
                v_eff = max(v, self.VOL_FLOOR)
                lev = max(0.0, min(self.TARGET_VOL / v_eff, self.MAX_POS))

                strength = abs(trend)
                if strength < self.WEAK_TREND:
                    lev *= self.WEAK_SCALE
                elif strength < self.STRONG_TREND:
                    lev *= self.MID_SCALE

                raw_target = lev

        previous_target = self.target_weight
        self.target_weight = max(
            0.0,
            min(1.0, (1.0 - self.SMOOTH_ALPHA) * previous_target + self.SMOOTH_ALPHA * raw_target),
        )

        if v_med is None and self.target_weight <= EPS and previous_target <= EPS:
            return None

        metadata: dict[str, float] = {"target_weight": self.target_weight}
        if self.SL_PCT is not None and self.target_weight > 0.0:
            metadata["stop_loss"] = px * (1.0 - float(self.SL_PCT))
        if self.TP_PCT is not None and self.target_weight > 0.0:
            metadata["take_profit"] = px * (1.0 + float(self.TP_PCT))

        return Signal(
            time_stamp=bar.time_stamp,
            symbol=bar.symbol,
            signal_type=SignalType.BUY if self.target_weight > previous_target else SignalType.SELL,
            ref_price=px,
            quantity=None,
            confidence=1.0,
            metadata=metadata,
        )
