from test_backtester import test_prepare_data, test_apply_strategy, test_run_backtest
from test_get_data_script import test_get_data_df
from test_config import test_load_config

if __name__ == "__main__": # TODO: Use a better testing method
    # Backtester tests
    test_prepare_data()
    test_apply_strategy()
    test_run_backtest()

    # Data fetching tests
    test_get_data_df()

    # Config loading
    test_load_config()
