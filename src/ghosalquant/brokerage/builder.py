from ..brokerage.broker_base import Broker
from .simulated import SimBroker
from .ibkr import IBKRBroker
from ..util.config import BrokerType, BrokerConfig, ConfigError


def build_broker(broker_config: BrokerConfig) -> Broker:
    if broker_config.type == BrokerType.SIMULATED:
        return SimBroker()
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
