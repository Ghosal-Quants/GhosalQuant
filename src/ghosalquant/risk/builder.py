from typing import Any

from .risk_manager import RiskManager
from .rules.rule_confidence_sizer import ConfidenceSizer
from .rules.rule_max_open_positions import MaxOpenPositions
from .rules.rule_regime_filter import RegimeFilter
from .rules.rule_drawdown_killswitch import DrawdownKillSwitch
from .rules.rule_correlation_filter import CorrelationFilter
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
    return CorrelationFilter(
        lookback=int(p.get("lookback", 60)),
        threshold=float(p.get("threshold", 0.75)),
    )


_RULES = {
    "MaxOpenPositions": make_max_open_positions,
    "ConfidenceSizer": make_confidence_sizer,
    "RegimeFilter": make_regime_filter,
    "DrawdownKillSwitch": make_drawdown_killswitch,
    "CorrelationFilter": make_correlation_filter,
}


def build_risk_manager(risk_config: RiskConfig) -> RiskManager:
    rules = []
    for rc in risk_config.rules:
        factory = _RULES.get(rc.name)
        if factory is None:
            raise ValueError(f"Unknown risk rule '{rc.name}'.")

        rules.append(factory(rc.params))

    return RiskManager(rules=rules)