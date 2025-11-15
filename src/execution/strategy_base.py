from core.types import Bar
from core.types import Signal

class Strategy:
    def on_start(self) -> None: ...
    def on_end(self) -> None: ...
    def on_market_data(self, bar: Bar) -> Signal: ...





