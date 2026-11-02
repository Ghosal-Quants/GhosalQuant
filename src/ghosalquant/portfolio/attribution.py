from dataclasses import dataclass
from datetime import datetime

from ..core import blackscholes
from ..core.contracts import Contract

DAYS_PER_YEAR = 365.0
VOL_POINT = 100.0


@dataclass(frozen=True, slots=True)
class Attribution:
    """
    Where a position's money came from, in account currency.

    ``unexplained`` is the total less the four terms: higher-order moves, and whatever a single
    vol does not account for. A large one means the split is not to be trusted.
    """

    delta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0
    theta: float = 0.0
    unexplained: float = 0.0

    @property
    def explained(self) -> float:
        return self.delta + self.gamma + self.vega + self.theta

    @property
    def total(self) -> float:
        return self.explained + self.unexplained

    def __add__(self, other: "Attribution") -> "Attribution":
        return Attribution(
            self.delta + other.delta,
            self.gamma + other.gamma,
            self.vega + other.vega,
            self.theta + other.theta,
            self.unexplained + other.unexplained,
        )


def attribute(
    contract: Contract,
    quantity: float,
    *,
    spot_before: float,
    spot_after: float,
    vol_before: float,
    vol_after: float,
    at_before: datetime,
    at_after: datetime,
    value_before: float,
    value_after: float,
    rate: float = 0.0,
    dividend: float = 0.0,
) -> Attribution:
    """
    Split one position's change in value over one step into its causes.

    Greeks are taken at the start of the step, which is what a hedge would have been sized on.
    """
    exposure = float(quantity) * contract.multiplier
    if exposure == 0.0:
        return Attribution()

    sensitivities = blackscholes.greeks(
        contract, spot_before, vol_before, at_before, rate=rate, dividend=dividend
    )
    move = spot_after - spot_before
    days = (at_after - at_before).total_seconds() / (DAYS_PER_YEAR * 24 * 3600) * DAYS_PER_YEAR

    delta = sensitivities.delta * move * exposure
    gamma = 0.5 * sensitivities.gamma * move * move * exposure
    # Vega is quoted per vol point and theta per day, so the moves have to be in those units too.
    vega = sensitivities.vega * (vol_after - vol_before) * VOL_POINT * exposure
    theta = sensitivities.theta * days * exposure

    total = (value_after - value_before) * exposure
    return Attribution(delta, gamma, vega, theta, total - (delta + gamma + vega + theta))
