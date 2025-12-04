from execution.strategy_base import Strategy
from core.types import Bar, Signal, SignalType


class ExampleStrategy(Strategy):
    """
    Applies a simple test strategy with defined Stop Loss (SL) and Take Profit (TP).

    SignalType.BUY if Close < Previous Close.
    SignalType.SELL if Close > Previous Close.
    Otherwise, SignalType.NOOP.

    SL is fixed at 1.0% and TP at 2.0% of the entry price.
    """

    _SL_PERCENT: float = 0.01
    _TP_PERCENT: float = 0.02

    def __init__(self):
        super().__init__()
        self.prev_bar: Bar | None = None

    def on_start(self):
        ...

    def on_end(self):
        ...

    def on_market_data(self, bar: Bar) -> Signal:
        if self.prev_bar is None:
            self.prev_bar = bar
            return Signal(signal_type=SignalType.NOOP, time_stamp=bar.time_stamp, symbol=bar.symbol, price=bar.close)

        signal_type: SignalType

        if bar.close > self.prev_bar.close:
            signal_type = SignalType.SELL
        elif bar.close < self.prev_bar.close:
            signal_type = SignalType.BUY
        else:
            self.prev_bar = bar
            return Signal(signal_type=SignalType.NOOP, time_stamp=bar.time_stamp, symbol=bar.symbol, price=bar.close)

        self.prev_bar = bar
        confidence = 0.99                                                 # Actual strategies would employ more robust confidence calculations
        quantity = super().calculate_quantity(confidence= confidence)     # Basic quantity calculator in base class or strategies can override it and implement their own 

        # Handle stop loss and take profit levels only for BUY/SELL

        if signal_type == SignalType.BUY:
            sl = bar.close * (1 - self._SL_PERCENT)
            tp = bar.close * (1 + self._TP_PERCENT)
        elif signal_type == SignalType.SELL:
            sl = bar.close * (1 + self._SL_PERCENT)
            tp = bar.close * (1 - self._TP_PERCENT)
        else:
            raise ValueError("Invalid signal type for SL/TP calculation.") # Should be unreachable

        
        return Signal(
            time_stamp= bar.time_stamp,
            symbol= bar.symbol,
            signal_type= signal_type,
            price= bar.close,
            confidence= confidence,
            quantity= quantity,
            metadata= {
                "stop_loss": sl,
                "take_profit": tp
            }
        )
