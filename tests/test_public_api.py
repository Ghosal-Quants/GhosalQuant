import ghosalquant
import ghosalquant.core.types as core_types
from ghosalquant.core import metadata_keys

EXPORTED_TYPES = (
    "BacktestKPMs", "Bar", "BookLevel", "EquityPoint", "Fill", "Liquidity", "Order", "OrderBook", "PublicTrade",
    "Signal", "SignalType", "Trade",
)


def test_the_types_a_strategy_uses_import_from_the_package():
    from ghosalquant import Bar, Signal, SignalType, Strategy  # noqa: F401

    assert all(hasattr(ghosalquant, name) for name in ghosalquant.__all__)
    for name in EXPORTED_TYPES:
        assert getattr(ghosalquant, name) is getattr(core_types, name)
    assert ghosalquant.metadata_keys is metadata_keys
    assert ghosalquant.types is core_types
