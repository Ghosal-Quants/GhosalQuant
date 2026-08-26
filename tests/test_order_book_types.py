import warnings
from datetime import datetime

import pytest

from ghosalquant.core import metadata_keys
from ghosalquant.core.types import (
    BookLevel,
    Liquidity,
    OpenOrderState,
    OpenOrderView,
    Order,
    OrderBook,
    PublicTrade,
    Signal,
    SignalType,
)


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _book(bids, asks, **kwargs) -> OrderBook:
    return OrderBook(
        time_stamp=_ts("2025-11-15 09:30:00"),
        symbol="TEST",
        bids=tuple(BookLevel(price, qty) for price, qty in bids),
        asks=tuple(BookLevel(price, qty) for price, qty in asks),
        **kwargs,
    )


def test_book_level_validation():
    BookLevel(100.0, 0.0)
    with pytest.raises(ValueError):
        BookLevel(0.0, 10.0)
    with pytest.raises(ValueError):
        BookLevel(100.0, -1.0)
    with pytest.raises(TypeError):
        BookLevel("100", 1.0)


def test_order_book_derived_fields_are_cached_and_excluded_from_equality():
    book = _book([(100.0, 10), (99.5, 20)], [(100.5, 5), (101.0, 15)], sequence=7)

    assert book.best_bid == BookLevel(100.0, 10)
    assert book.best_ask == BookLevel(100.5, 5)
    assert book.spread == pytest.approx(0.5)
    assert book.mid_price == pytest.approx(100.25)
    assert book.spread_bps == pytest.approx(0.5 / 100.25 * 10_000.0)
    assert book.bid_quantity == 30.0
    assert book.ask_quantity == 20.0
    assert book.is_two_sided()
    assert not book.is_crossed()
    assert book.imbalance() == pytest.approx((30.0 - 20.0) / 50.0)
    assert book.imbalance(depth=1) == pytest.approx((10.0 - 5.0) / 15.0)

    same = _book([(100.0, 10), (99.5, 20)], [(100.5, 5), (101.0, 15)], sequence=7)
    assert book == same
    assert hash(book) == hash(same)
    assert "best_bid" not in repr(book)


def test_order_book_accepts_lists_and_freezes_them_as_tuples():
    book = OrderBook(
        time_stamp=_ts("2025-11-15 09:30:00"),
        symbol="TEST",
        bids=[BookLevel(100.0, 1.0)],
        asks=[BookLevel(101.0, 1.0)],
    )
    assert isinstance(book.bids, tuple)
    assert isinstance(book.asks, tuple)


def test_order_book_rejects_misordered_levels():
    with pytest.raises(ValueError):
        _book([(99.0, 1), (100.0, 1)], [])
    with pytest.raises(ValueError):
        _book([], [(101.0, 1), (100.5, 1)])
    with pytest.raises(ValueError):
        _book([(100.0, 1), (100.0, 1)], [])


def test_order_book_one_sided_and_empty_books():
    bid_only = _book([(100.0, 10)], [])
    assert bid_only.best_ask is None
    assert bid_only.spread is None
    assert bid_only.mid_price is None
    assert bid_only.spread_bps is None
    assert not bid_only.is_two_sided()
    assert not bid_only.is_crossed()
    assert bid_only.imbalance() == pytest.approx(1.0)

    empty = _book([], [])
    assert empty.imbalance() is None
    assert empty.bid_quantity == 0.0


def test_order_book_detects_locked_and_crossed_books():
    assert _book([(100.0, 1)], [(100.0, 1)]).is_crossed()
    assert _book([(100.5, 1)], [(100.0, 1)]).is_crossed()


def test_order_book_replace_recomputes_derived_fields():
    book = _book([(100.0, 10)], [(100.5, 5)], sequence=1)
    later = book.replace(
        time_stamp=_ts("2025-11-15 09:30:01"),
        asks=(BookLevel(100.25, 5),),
        sequence=2,
    )

    assert later.sequence == 2
    assert later.spread == pytest.approx(0.25)
    assert later.mid_price == pytest.approx(100.125)
    assert book.spread == pytest.approx(0.5)  # the original is untouched


def test_public_trade_defaults_and_validation():
    trade = PublicTrade(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", price=100.0, quantity=3.0)
    assert trade.observed_at is None
    assert trade.effective_observed_at == trade.time_stamp
    assert trade.aggressor is None

    observed = PublicTrade(
        time_stamp=_ts("2025-11-15 09:30:00"),
        symbol="TEST",
        price=100.0,
        quantity=3.0,
        trade_id="t-1",
        aggressor=SignalType.BUY,
        observed_at=_ts("2025-11-15 09:30:05"),
    )
    assert observed.effective_observed_at == _ts("2025-11-15 09:30:05")

    with pytest.raises(ValueError):
        PublicTrade(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", price=100.0, quantity=0.0)
    with pytest.raises(TypeError):
        PublicTrade(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", price=100.0, quantity=1.0, aggressor="BUY")


def test_signal_with_metadata_merges_and_preserves_original():
    signal = Signal(
        time_stamp=_ts("2025-11-15 09:30:00"),
        symbol="TEST",
        signal_type=SignalType.BUY,
        ref_price=100.0,
        metadata={metadata_keys.REASON: "entry"},
    )
    tagged = signal.with_metadata(**{metadata_keys.LIMIT_PRICE: 99.5, metadata_keys.REASON: "improved"})

    assert tagged.metadata == {metadata_keys.REASON: "improved", metadata_keys.LIMIT_PRICE: 99.5}
    assert signal.metadata == {metadata_keys.REASON: "entry"}
    assert tagged.ref_price == signal.ref_price

    bare = Signal(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", signal_type=SignalType.SELL, ref_price=1.0)
    assert bare.with_metadata(x=1).metadata == {"x": 1}


def test_order_limit_price_field_metadata_fallback_and_effective_price():
    market = Order(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", side=SignalType.BUY, quantity=1.0, ref_price=100.0)
    assert market.limit_price is None
    assert market.declared_limit_price is None
    assert not market.is_limit
    assert market.effective_limit_price == 100.0

    via_metadata = Order(
        time_stamp=_ts("2025-11-15 09:30:00"),
        symbol="TEST",
        side=SignalType.BUY,
        quantity=1.0,
        ref_price=100.0,
        metadata={metadata_keys.LIMIT_PRICE: 99.0},
    )
    assert via_metadata.limit_price is None
    assert via_metadata.declared_limit_price == 99.0
    assert via_metadata.is_limit
    assert via_metadata.effective_limit_price == 99.0

    via_field = Order(
        time_stamp=_ts("2025-11-15 09:30:00"),
        symbol="TEST",
        side=SignalType.BUY,
        quantity=1.0,
        ref_price=100.0,
        metadata={metadata_keys.LIMIT_PRICE: 99.0},
        limit_price=98.5,
        liquidity="maker",
    )
    assert via_field.declared_limit_price == 98.5  # the field wins over metadata
    assert via_field.liquidity is Liquidity.MAKER

    with pytest.raises(ValueError):
        Order(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", side=SignalType.BUY, quantity=1.0, ref_price=100.0, limit_price=0.0)


def test_open_order_view_properties():
    order = Order(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", side=SignalType.SELL, quantity=4.0, ref_price=100.0)
    view = OpenOrderView(
        broker_order_id="SIM-1",
        order=order,
        remaining_quantity=3.0,
        submitted_at=order.time_stamp,
        limit_price=101.0,
        liquidity=Liquidity.MAKER,
    )
    assert view.symbol == "TEST"
    assert view.side is SignalType.SELL
    assert view.notional == pytest.approx(303.0)

    unpriced = OpenOrderView(broker_order_id="SIM-2", order=order, remaining_quantity=4.0, submitted_at=order.time_stamp)
    assert unpriced.notional == pytest.approx(400.0)


def test_open_order_state_is_deprecated():
    order = Order(time_stamp=_ts("2025-11-15 09:30:00"), symbol="TEST", side=SignalType.BUY, quantity=1.0, ref_price=100.0)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        OpenOrderState(broker_order_id="x", order=order, submitted_at=order.time_stamp, remaining_qty=1.0)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_liquidity_is_a_string_enum():
    assert Liquidity.MAKER == "maker"
    assert Liquidity("taker") is Liquidity.TAKER
