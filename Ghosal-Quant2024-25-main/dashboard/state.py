import utils

def load_portfolio_data():
    global active_performance, passive_performance, portfolio_comparisons, passiveWeeklyComparisons, activeWeeklyComparisons
    active_performance, passive_performance, portfolio_comparisons, passiveWeeklyComparisons, activeWeeklyComparisons = utils.loadData()
    return active_performance, passive_performance, portfolio_comparisons, passiveWeeklyComparisons, activeWeeklyComparisons