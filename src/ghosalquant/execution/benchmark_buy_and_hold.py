from ghosalquant import Strategy
from ghosalquant.core.types import Bar, Signal, SignalType

class BenchmarkBuyAndHold(Strategy):
    """
    Buy and hold benchmark strategy.
    - On a bar received with an unseen symbol, sends a BUY signal.
    - After entering, emits no further signals for that symbol
    """

    _USE_CATASTROPHIC_SL: bool = True
    _CATASTROPHIC_SL_PERCENT: float = 0.35  # 35% below entry in case of extreme drawdown

    def __init__(self):
        super().__init__()
        self._entered: dict[str, bool] = {}
        self._entry_price: dict[str, float] = {}

    def on_start(self):
        ...

    def on_end(self):
        ...

    def on_market_data(self, bar: Bar) -> Signal | None:
        symbol = bar.symbol

        if not self._entered.get(symbol, False):
            self._entered[symbol] = True
            self._entry_price[symbol] = float(bar.close)

            confidence = 1.0
            quantity = super().calculate_quantity(confidence=confidence)

            metadata: dict[str, float] = {}

            if self._USE_CATASTROPHIC_SL:
                sl = bar.close * (1.0 - self._CATASTROPHIC_SL_PERCENT)
                metadata["stop_loss"] = sl

            return Signal(
                time_stamp=bar.time_stamp,
                symbol=symbol,
                signal_type=SignalType.BUY,
                ref_price=bar.close,
                confidence=confidence,
                quantity=quantity,
                metadata=metadata if metadata else None,
            )

        return None
