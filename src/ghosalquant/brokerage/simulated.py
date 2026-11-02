from .commissions import PerShareCommission
from .fills import BarParticipationFill
from .simulation import SimulatedBroker


class SimBroker(SimulatedBroker):
    """
    Bar-driven simulated broker with latency, liquidity caps, and fill modelling.

    A preset of ``SimulatedBroker`` wired to ``BarParticipationFill``: orders fill against bar
    volume at a price built from the bar open plus half-spread, slippage and participation impact.
    Order books and public trades advance the clock but do not fill anything — use
    ``OrderBookSimBroker`` for level-2 data, which queues resting orders properly instead of
    assuming they cross.
    """

    def __init__(
        self,
        *,
        spread_bps: float = 2.0,
        slippage_mean_bps: float = 0.0,
        slippage_std_bps: float = 1.0,
        commission_per_share: float = 0.0,
        partial_fill_probability: float = 0.0,
        reject_probability: float = 0.0,
        max_fill_pct_of_bar_volume: float = 0.25,
        impact_bps_at_full_participation: float = 10.0,
        min_latency_ms: int = 0,
        max_latency_ms: int = 0,
        max_order_age_events: int | None = None,
        max_order_age_seconds: float | None = None,
        seed: int | None = None,
    ) -> None:
        super().__init__(
            BarParticipationFill(
                spread_bps=spread_bps,
                slippage_mean_bps=slippage_mean_bps,
                slippage_std_bps=slippage_std_bps,
                partial_fill_probability=partial_fill_probability,
                max_fill_pct_of_bar_volume=max_fill_pct_of_bar_volume,
                impact_bps_at_full_participation=impact_bps_at_full_participation,
            ),
            commission=PerShareCommission(commission_per_share),
            min_latency_ms=min_latency_ms,
            max_latency_ms=max_latency_ms,
            reject_probability=reject_probability,
            max_order_age_events=max_order_age_events,
            max_order_age_seconds=max_order_age_seconds,
            fill_without_market_data=True,
            order_id_prefix="SIM",
            seed=seed,
        )

    @property
    def spread_bps(self) -> float:
        return self.fill_model.spread_bps

    @property
    def slippage_mean_bps(self) -> float:
        return self.fill_model.slippage_mean_bps

    @property
    def slippage_std_bps(self) -> float:
        return self.fill_model.slippage_std_bps

    @property
    def partial_fill_probability(self) -> float:
        return self.fill_model.partial_fill_probability

    @property
    def max_fill_pct_of_bar_volume(self) -> float:
        return self.fill_model.max_fill_pct_of_bar_volume

    @property
    def impact_bps_at_full_participation(self) -> float:
        return self.fill_model.impact_bps_at_full_participation

    @property
    def commission_per_share(self) -> float:
        return self.commission.per_share
