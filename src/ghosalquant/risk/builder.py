from typing import Any

from .risk_manager import RiskManager
from .rules.rule_confidence_sizer import ConfidenceSizer
from .rules.rule_max_open_positions import MaxOpenPositions
from .rules.rule_regime_filter import RegimeFilter
from .rules.rule_drawdown_killswitch import DrawdownKillSwitch
from .rules.rule_correlation_filter import CorrelationFilter
from .rules.rule_integer_quantity import IntegerQuantity
from .rules.rule_max_symbol_exposure import MaxSymbolExposure
from .rules.rule_signal_quantity_cap import SignalQuantityCap
from .rules.rule_symbol_capital_sizer import SymbolCapitalFractionSizer
from .rules.rule_tick_rounding import TickRounding
from .cash_allocation import CashAllocationConfig, CashAllocationRule
from ..util.config import RiskConfig, RiskRuleConfig


# TODO: This is absolutely DISGUSTING but I can't think of a better way to do it architecturally :,(
def make_max_open_positions(p: dict[str, Any]) -> MaxOpenPositions:
    return MaxOpenPositions(
        max_open_positions=int(p.get("max_open_positions", 3))
    )


def make_confidence_sizer(p: dict[str, Any]) -> ConfidenceSizer:
    return ConfidenceSizer(
        min_quantity=float(p.get("min_quantity", 0.0)),
        max_quantity=float(p.get("max_quantity", 10.0)),
    )


def make_regime_filter(p: dict[str, Any]) -> RegimeFilter:
    blocked = p.get("blocked_regimes", ["NOISE"])
    penalty = p.get("penalty_regimes", {"CHOPPY": 0.6})
    return RegimeFilter(
        blocked_regimes=list(blocked),
        penalty_regimes=dict(penalty),
    )


def make_drawdown_killswitch(p: dict[str, Any]) -> DrawdownKillSwitch:
    return DrawdownKillSwitch(
        reduce_threshold=float(p.get("reduce_threshold", -0.08)),
        kill_threshold=float(p.get("kill_threshold", -0.15)),
        reduce_multiplier=float(p.get("reduce_multiplier", 0.5)),
    )


def make_correlation_filter(p: dict[str, Any]) -> CorrelationFilter:
    ramp_up = p.get("ramp_up")
    return CorrelationFilter(
        lookback=int(p.get("lookback", 60)),
        threshold=float(p.get("threshold", 0.75)),
        ramp_up=None if ramp_up is None else int(ramp_up),
    )


def make_integer_quantity(p: dict[str, Any]) -> IntegerQuantity:
    return IntegerQuantity(lot_size=float(p.get("lot_size", 1.0)))


def make_signal_quantity_cap(p: dict[str, Any]) -> SignalQuantityCap:
    key = p.get("metadata_key")
    return SignalQuantityCap() if key is None else SignalQuantityCap(metadata_key=str(key))


def make_max_symbol_exposure(p: dict[str, Any]) -> MaxSymbolExposure:
    return MaxSymbolExposure(
        capital=float(p["capital"]),
        max_notional_fraction=float(p["max_notional_fraction"]),
        taker_fee_bps=float(p.get("taker_fee_bps", 0.0)),
        reference_capital=_optional_float(p.get("reference_capital")),
    )


def make_symbol_capital_sizer(p: dict[str, Any]) -> SymbolCapitalFractionSizer:
    return SymbolCapitalFractionSizer(
        capital=float(p["capital"]),
        max_notional_fraction_by_symbol={
            str(symbol): float(fraction)
            for symbol, fraction in (p.get("max_notional_fraction_by_symbol") or {}).items()
        },
        default_max_notional_fraction=_optional_float(p.get("default_max_notional_fraction")),
        min_quantity=float(p.get("min_quantity", 0.0)),
        scale_by_confidence=bool(p.get("scale_by_confidence", True)),
        reference_capital=_optional_float(p.get("reference_capital")),
    )


def make_cash_allocation(p: dict[str, Any]) -> CashAllocationRule:
    config = CashAllocationConfig(
        total_fraction=float(p.get("total_fraction", 0.9)),
        max_symbol_fraction=float(p.get("max_symbol_fraction", 0.3)),
        bucket_fractions={
            str(bucket): float(fraction)
            for bucket, fraction in (p.get("bucket_fractions") or {}).items()
        },
        max_order_fractions={
            str(bucket): float(fraction)
            for bucket, fraction in (p.get("max_order_fractions") or {}).items()
        },
        default_bucket_fraction=float(p.get("default_bucket_fraction", 1.0)),
        default_max_order_fraction=float(p.get("default_max_order_fraction", 1.0)),
    )
    return CashAllocationRule(
        config,
        capital=float(p["capital"]),
        reference_capital=_optional_float(p.get("reference_capital")),
    )


def make_tick_rounding(p: dict[str, Any]) -> TickRounding:
    return TickRounding(
        tick_size=float(p.get("tick_size", 0.01)),
        tick_size_by_symbol={
            str(symbol): float(size)
            for symbol, size in (p.get("tick_size_by_symbol") or {}).items()
        },
    )


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


_RULES = {
    "MaxOpenPositions": make_max_open_positions,
    "ConfidenceSizer": make_confidence_sizer,
    "RegimeFilter": make_regime_filter,
    "DrawdownKillSwitch": make_drawdown_killswitch,
    "CorrelationFilter": make_correlation_filter,
    "IntegerQuantity": make_integer_quantity,
    "SignalQuantityCap": make_signal_quantity_cap,
    "MaxSymbolExposure": make_max_symbol_exposure,
    "SymbolCapitalFractionSizer": make_symbol_capital_sizer,
    "CashAllocation": make_cash_allocation,
    "TickRounding": make_tick_rounding,
}


def build_risk_manager(risk_config: RiskConfig) -> RiskManager:
    rules = []
    for rc in risk_config.rules:
        factory = _RULES.get(rc.name)
        if factory is None:
            raise ValueError(f"Unknown risk rule '{rc.name}'.")

        rules.append(factory(rc.params))

    return RiskManager(rules=rules)