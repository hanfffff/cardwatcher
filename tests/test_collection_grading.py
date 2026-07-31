"""Tests for grading in app/collection.py.

Covers the two things grading changes about a collection: a slab is a separate
holding from the raw card, and it must never be valued off raw listings.
"""
import time

from app.collection import (
    CollectionItem,
    calculate_collection_price,
    identity_of,
)
from tests.conftest import make_listing


class _FakePage:
    def __init__(self, listings):
        self.listings = listings


def _card_with_raw_and_slabs():
    now = time.time()
    return _FakePage([
        make_listing(seller="a", price=40.0, condition="NM", date=now, first_date=now - 86400),
        make_listing(seller="b", price=45.0, condition="NM", date=now, first_date=now - 86400),
        make_listing(seller="c", price=800.0, condition="NM", date=now, first_date=now - 86400,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="d", price=900.0, condition="NM", date=now, first_date=now - 86400,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="e", price=300.0, condition="NM", date=now, first_date=now - 86400,
                     grade_company="PSA", grade=9.0),
    ])


def test_slab_is_priced_off_matching_slabs_only():
    page = _card_with_raw_and_slabs()

    price = calculate_collection_price(page, "NM", "English", 0, 0,
                                       grade_company="PSA", grade=10.0)

    assert price == 800.0     # lowest PSA 10, not the 40 EUR raw floor


def test_raw_card_is_priced_off_raw_listings_only():
    page = _card_with_raw_and_slabs()

    price = calculate_collection_price(page, "NM", "English", 0, 0)

    assert price == 40.0      # the slabs must not drag this up


def test_a_different_grade_is_a_different_product():
    page = _card_with_raw_and_slabs()

    assert calculate_collection_price(page, "NM", "English", 0, 0,
                                      grade_company="PSA", grade=9.0) == 300.0


def test_no_comparable_slab_reports_no_data_rather_than_a_raw_price():
    """0 is honest here. Falling back to the raw price would be off by ~20x."""
    page = _card_with_raw_and_slabs()

    assert calculate_collection_price(page, "NM", "English", 0, 0,
                                      grade_company="BGS", grade=10.0) == 0


def test_relaxed_condition_match_never_relaxes_the_grade():
    """The relaxed pass loosens condition and first_ed, but not grading."""
    now = time.time()
    page = _FakePage([
        # Same card one condition better, but raw.
        make_listing(seller="a", price=40.0, condition="MT", date=now, first_date=now - 86400),
    ])

    assert calculate_collection_price(page, "NM", "English", 0, 0,
                                      grade_company="PSA", grade=10.0) == 0
    # A raw holding does reach it via the relaxed pass.
    assert calculate_collection_price(page, "NM", "English", 0, 0) == 40.0


def test_recent_slab_sale_prices_a_holding_with_no_live_ask():
    """Graded supply is thin, so the sold window is far wider than for raw."""
    now = time.time()
    thirty_days_ago = now - 30 * 24 * 3600
    page = _FakePage([
        make_listing(seller="a", price=820.0, quantity=0, ended=True,
                     date=thirty_days_ago, first_date=now - 90 * 24 * 3600,
                     grade_company="PSA", grade=10.0),
    ])

    # Well outside the 7-day window a raw card uses.
    assert calculate_collection_price(page, "NM", "English", 0, 0,
                                      grade_company="PSA", grade=10.0) == 820.0


# --- item identity ------------------------------------------------------------

def test_grade_is_part_of_item_identity():
    raw = CollectionItem("Card", "NM", "English")
    slab = CollectionItem("Card", "NM", "English", grade_company="PSA", grade=10.0)

    assert raw.identity() != slab.identity()
    assert slab.identity() == identity_of("Card", "NM", "English", 0, 0, "PSA", 10.0)


def test_legacy_item_loads_as_raw():
    """A collection entry was typed in by the user, so a missing grade means raw
    -- unlike a listing, there is nothing un-inspected about it."""
    item = CollectionItem.from_dict({"canonical_name": "Card", "condition": "NM",
                                     "language": "English", "quantity": 1})

    assert item.grade_company == ""
    assert item.grade is None


def test_item_grade_round_trips():
    item = CollectionItem("Card", "NM", "English", grade_company="BGS", grade=9.5)
    restored = CollectionItem.from_dict(item.to_dict())

    assert restored.grade_company == "BGS"
    assert restored.grade == 9.5
    assert restored.identity() == item.identity()
