"""
The metadata contract shared by strategies, risk rules, and brokers.

``Signal.metadata`` and ``Order.metadata`` are free-form dicts. These constants name the keys the
framework itself reads or writes so that a strategy, a risk rule, and a broker agree on them.
Application code may add its own keys alongside these.

Set by strategies on a Signal (copied onto the Order by RiskManager):
``LIMIT_PRICE`` and ``LIQUIDITY`` predate the first-class fields of the same name and are still
honoured; ``ORDER_SLOT`` is a stable name a strategy uses to find its own resting order;
``MAX_QUANTITY`` is the strategy's own size cap; ``RISK_UNIT_COST`` is what one unit costs all in, when
that is more than the price; ``ALLOCATION_BUCKET`` names the cash bucket the order competes in; ``MAX_ORDER_AGE_SECONDS`` lets a broker expire
the order; ``REPLACE_ENTRY_ORDERS`` / ``REPLACE_EXIT_ORDERS`` cancel the symbol's resting orders on
the order's side, and the order waits until they have gone; ``CANCEL_REMAINDER_AFTER_FIRST_FILL`` gives a limit
IOC-like behaviour; ``TARGET_WEIGHT`` is the fraction of equity to hold in the symbol.

Written by the framework: ``CONFIDENCE`` (RiskManager copies ``Signal.confidence``),
``IDEMPOTENCY_KEY`` (live brokers stamp it before submission), ``TARGET_QUANTITY``
(the position a ``TARGET_WEIGHT`` resolved to).
"""

LIMIT_PRICE = "limit_price"
LIQUIDITY = "liquidity"
REASON = "reason"
ORDER_SLOT = "order_slot"
MAX_QUANTITY = "max_quantity"
RISK_UNIT_COST = "risk_unit_cost"
MAX_ORDER_AGE_SECONDS = "max_order_age_seconds"
REPLACE_ENTRY_ORDERS = "replace_entry_orders"
REPLACE_EXIT_ORDERS = "replace_exit_orders"
CANCEL_REMAINDER_AFTER_FIRST_FILL = "cancel_remainder_after_first_fill"
ALLOCATION_BUCKET = "allocation_bucket"
TARGET_WEIGHT = "target_weight"

CONFIDENCE = "confidence"
TARGET_QUANTITY = "target_quantity"
IDEMPOTENCY_KEY = "idempotency_key"
STOP_LOSS = "stop_loss"
TAKE_PROFIT = "take_profit"

__all__ = [
    "LIMIT_PRICE",
    "LIQUIDITY",
    "REASON",
    "ORDER_SLOT",
    "MAX_QUANTITY",
    "RISK_UNIT_COST",
    "MAX_ORDER_AGE_SECONDS",
    "REPLACE_ENTRY_ORDERS",
    "REPLACE_EXIT_ORDERS",
    "CANCEL_REMAINDER_AFTER_FIRST_FILL",
    "ALLOCATION_BUCKET",
    "TARGET_WEIGHT",
    "CONFIDENCE",
    "TARGET_QUANTITY",
    "IDEMPOTENCY_KEY",
    "STOP_LOSS",
    "TAKE_PROFIT",
]
