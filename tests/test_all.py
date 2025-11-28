from test_backtester import test_run_backtest, test_legacy_apply_strategy
from test_get_data_script import test_get_data_df
from test_config import test_load_config
from test_execution import test_chain_sinks, test_execute_strategy

if __name__ == "__main__": # TODO: Use a better testing method
    # execution tests
    test_chain_sinks()
    test_execute_strategy()

    # LegacyBacktester tests
    # TODO: Remove these eventually once we fully migrate away from LegacyBacktester
    test_run_backtest()
    test_legacy_apply_strategy()

    # Data fetching tests
    test_get_data_df()

    # Config loading
    test_load_config()
