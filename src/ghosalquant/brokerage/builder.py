from ..brokerage.broker_base import Broker
from .simulated import SimBroker
from .ibkr import IBKRBroker
from ..util.config import BrokerType, BrokerConfig, ConfigError


def build_broker(broker_config: BrokerConfig) -> Broker:
    if not isinstance(broker_config, BrokerConfig):
        raise ConfigError("A valid broker configuration must be provided.")

    if broker_config.type == BrokerType.SIMULATED:
        cfg = broker_config.sim
        if cfg is None:
            return SimBroker()
        return SimBroker(
            spread_bps=cfg.spread_bps,
            slippage_mean_bps=cfg.slippage_mean_bps,
            slippage_std_bps=cfg.slippage_std_bps,
            commission_per_share=cfg.commission_per_share,
            partial_fill_probability=cfg.partial_fill_probability,
            reject_probability=cfg.reject_probability,
            max_fill_pct_of_bar_volume=cfg.max_fill_pct_of_bar_volume,
            impact_bps_at_full_participation=cfg.impact_bps_at_full_participation,
            min_latency_ms=cfg.min_latency_ms,
            max_latency_ms=cfg.max_latency_ms,
            seed=cfg.seed,
        )
    elif broker_config.type == BrokerType.IBKR:
        if broker_config.ibkr is None:
            raise ConfigError("IBKR configuration is missing.")
        return IBKRBroker(
            host=broker_config.ibkr.host,
            port=broker_config.ibkr.port,
            client_id=broker_config.ibkr.client_id,
            mode=broker_config.mode
        )
    else:
        raise ValueError(f"Unsupported broker type: {broker_config.type}")
