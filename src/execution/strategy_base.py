# from src.core.types import Bar
# from src.core.types import Signal

from core.types import Bar
from core.types import Signal

class Strategy:
    def on_start(self): ...
    def on_end(self): ...
    def on_market_data(self, bar: Bar) -> Signal: ...





