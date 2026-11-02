from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal


def floor_to_tick(price: float, tick_size: float) -> float:
    """The nearest price the venue accepts at or below ``price``."""
    return _to_tick(price, tick_size, ROUND_FLOOR)


def ceil_to_tick(price: float, tick_size: float) -> float:
    """The nearest price the venue accepts at or above ``price``."""
    return _to_tick(price, tick_size, ROUND_CEILING)


def round_to_tick(price: float, tick_size: float) -> float:
    """The nearest price the venue accepts, in either direction."""
    return _to_tick(price, tick_size, ROUND_HALF_UP)


def _to_tick(price: float, tick_size: float, rounding: str) -> float:
    # Decimal, not round(): a float cannot hold 193.375, so round() picks the wrong tick.
    if tick_size <= 0.0:
        raise ValueError("tick_size must be > 0")
    tick = Decimal(str(tick_size))
    ticks = (Decimal(str(price)) / tick).to_integral_value(rounding=rounding)
    return float(ticks * tick)
