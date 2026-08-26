from examples.example_order_book_strategy import JoinTheBidStrategy, build_events, run
from ghosalquant.core.types import OrderBook, PublicTrade


def test_example_events_are_time_ordered_and_two_sided():
    events = build_events(seed=1, steps=50)
    books = [event for event in events if isinstance(event, OrderBook)]
    prints = [event for event in events if isinstance(event, PublicTrade)]

    assert len(books) == 50
    assert all(book.is_two_sided() and not book.is_crossed() for book in books)
    assert all(earlier.time_stamp < later.time_stamp for earlier, later in zip(books, books[1:]))
    assert all(earlier.time_stamp < later.time_stamp for earlier, later in zip(prints, prints[1:]))


def test_example_runs_end_to_end():
    strategy, portfolio, kpms = run(seed=7, steps=300)

    assert isinstance(strategy, JoinTheBidStrategy)
    assert len(strategy.fills) > 0
    assert kpms.total_trades == len(list(portfolio.closed_trades))
    # Equity points are bounded by the 5-second snapshot interval plus one per fill.
    assert len(list(portfolio.equity_curve)) <= 300 // 5 + 1 + 2 * len(strategy.fills)
