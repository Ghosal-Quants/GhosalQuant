from src.util.config import load_config, Config, SystemConfig, TimeConfig

def test_load_config():
    config: Config = load_config("test_config.yaml")

    assert isinstance(config, Config)
    assert isinstance(config.system, SystemConfig)
    assert isinstance(config.time_range, TimeConfig)

    assert config.system.symbols == ["AAPL"]
    assert config.system.main_timeframe == "H1"
    assert config.system.execution_timeframe == "M5"
    assert config.system.fresh_data is True

    assert config.time_range.start_date.isoformat() == "2023-01-01"
    assert config.time_range.end_date.isoformat() == "2024-06-30"
    assert config.time_range.session_start_time.isoformat() == "09:00:00"
    assert config.time_range.session_end_time.isoformat() == "17:00:00"