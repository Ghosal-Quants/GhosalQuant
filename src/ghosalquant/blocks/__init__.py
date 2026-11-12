"""
Strategies described as data, for visual editors: a JSON spec, its validator, and the
``Strategy`` that runs it. See ``ghosalquant.blocks.schema`` for the spec.
"""

from .runner import bars_from_payload, run_spec, run_spec_async
from .schema import SPEC_VERSION, SpecError, validate_spec
from .strategy import BlockStrategy

__all__ = [
    "SPEC_VERSION",
    "BlockStrategy",
    "SpecError",
    "bars_from_payload",
    "run_spec",
    "run_spec_async",
    "validate_spec",
]
