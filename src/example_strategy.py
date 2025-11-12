from execution.strategy_base import Strategy
from core.types import Bar, Signal, SignalType


class ExampleStrategy(Strategy):
    '''
    A simple example strat that generates signals based on the previous closing price:
        - if current bar closes higher than previous -> SELL signal
        - if current bar closes lower than previous -> BUY signal
    * this is just a minimal implementation to exemplify how the interface would be used
    '''
    def __init__(self):
        self.prev_bar: Bar | None = None

    def on_start(self):
        print("Strategy started.")
        self.prev_bar = None

    def on_end(self):
        print("Strategy finished.")

    def on_market_data(self, bar: Bar) -> Signal | None:
        if self.prev_bar is None:
            self.prev_bar = bar
            return None  

        prev = self.prev_bar
        signal_type: SignalType | None = None

        if bar.close > prev.close:
            signal_type = SignalType.SELL
        elif bar.close < prev.close:
            signal_type = SignalType.BUY
        else:
            self.prev_bar = bar
            return None

        self.prev_bar = bar

        print("Signal Type: ", signal_type, ", Timestamp: ", bar.time_stamp, ", Symbol: ", bar.symbol, "Confidence: ", 1)

        return Signal(time_stamp=bar.time_stamp, symbol=bar.symbol, signal_type=signal_type)