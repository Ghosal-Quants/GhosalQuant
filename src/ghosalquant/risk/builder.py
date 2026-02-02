from typing import Any

from .risk_manager import RiskManager
from .rules.rule_confidence_sizer import ConfidenceSizer
from .rules.rule_max_open_positions import MaxOpenPositions
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


_RULES = {
    "MaxOpenPositions": make_max_open_positions,
    "ConfidenceSizer": make_confidence_sizer,
}


def build_risk_manager(risk_config: RiskConfig) -> RiskManager:
    rules = []
    for rc in risk_config.rules:
        factory = _RULES.get(rc.name)
        if factory is None:
            raise ValueError(f"Unknown risk rule '{rc.name}'.")

        rules.append(factory(rc.params))

    return RiskManager(rules=rules)