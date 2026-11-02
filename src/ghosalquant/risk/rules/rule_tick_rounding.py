from ...core import metadata_keys
from ...core.types import SignalType
from ...util.rounding import ceil_to_tick, floor_to_tick
from ..types import OrderDraft, RiskContext, RiskRule


class TickRounding(RiskRule):
    """
    Round a limit price to one the venue will accept.

    A quote from a book mid, or a price scaled by a fraction, lands between ticks and is rejected
    outright. Rounding is directional so it can only move the price in the trader's favour: a buy
    floors, a sell ceilings.
    """

    def __init__(
        self,
        tick_size: float = 0.01,
        *,
        tick_size_by_symbol: dict[str, float] | None = None,
    ) -> None:
        if tick_size <= 0.0:
            raise ValueError("tick_size must be > 0")
        self.tick_size = float(tick_size)
        self.tick_size_by_symbol = {
            str(symbol): float(size) for symbol, size in (tick_size_by_symbol or {}).items()
        }
        for symbol, size in self.tick_size_by_symbol.items():
            if size <= 0.0:
                raise ValueError(f"tick size for {symbol} must be > 0")

    def apply(self, draft: OrderDraft, context: RiskContext) -> bool:
        if draft.limit_price is None:
            return True
        tick = self.tick_size_by_symbol.get(draft.symbol, self.tick_size)
        to_tick = floor_to_tick if draft.side is SignalType.BUY else ceil_to_tick
        draft.limit_price = to_tick(float(draft.limit_price), tick)
        if metadata_keys.LIMIT_PRICE in draft.metadata:
            draft.metadata[metadata_keys.LIMIT_PRICE] = draft.limit_price
        return draft.limit_price > 0.0
