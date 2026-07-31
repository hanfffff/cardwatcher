"""Tests for app/listing.py — tooltip fallback, row parsing, JSON round-trip."""
from bs4 import BeautifulSoup

from app.listing import Listing, tooltip_label
from tests.conftest import make_listing


# --- tooltip_label fallbacks ------------------------------------------------

def _span(html):
    return BeautifulSoup(html, "html.parser").find("span")


def test_tooltip_label_none_tag():
    assert tooltip_label(None) is None


def test_tooltip_label_prefers_aria_label():
    tag = _span('<span aria-label="A" title="B" data-bs-original-title="C"></span>')
    assert tooltip_label(tag) == "A"


def test_tooltip_label_falls_back_to_title():
    # Server-rendered case: only `title` is present (no aria-label yet).
    tag = _span('<span title="Artikelstandort: Deutschland"></span>')
    assert tooltip_label(tag) == "Artikelstandort: Deutschland"


def test_tooltip_label_falls_back_to_data_bs_original_title():
    tag = _span('<span data-bs-original-title="Englisch"></span>')
    assert tooltip_label(tag) == "Englisch"


def test_tooltip_label_missing_returns_none():
    tag = _span("<span></span>")
    assert tooltip_label(tag) is None


# --- parse_from_row ---------------------------------------------------------

def _row(html, row_id):
    return BeautifulSoup(html, "html.parser").find(id=row_id)


def test_parse_from_row_server_rendered(listing_rows_html):
    """Row with title-only tooltips must parse (the aria-label regression)."""
    listing = Listing()
    listing.parse_from_row(_row(listing_rows_html, "row-server-rendered"))

    assert listing.seller.name == "GermanSeller"
    assert listing.seller.country == "Item location: Germany"
    assert listing.language == "English"
    assert listing.condition == "NM"
    assert listing.comment == "near mint. ships fast"  # comma normalized to '.'
    assert listing.price == 12.5
    assert listing.quantity == 3
    assert listing.first_ed == 0
    assert listing.reverse_holo == 0


def test_parse_from_row_post_js_with_markers(listing_rows_html):
    listing = Listing()
    listing.parse_from_row(_row(listing_rows_html, "row-post-js"))

    assert listing.seller.name == "JpnSeller"
    assert listing.seller.country == "Item location: Japan"
    assert listing.language == "Japanese"
    assert listing.condition == "EX"
    assert listing.price == 1499.0
    assert listing.quantity == 1
    assert listing.first_ed == 1
    assert listing.reverse_holo == 1


# --- to_json / from_json round-trip -----------------------------------------

def test_to_json_from_json_round_trip():
    original = make_listing(
        seller="alice", country="Item location: Germany", language="English",
        condition="NM", price=42.5, quantity=2, date=1700000000.0,
        first_date=1690000000.0, ended=False, first_ed=1, reverse_holo=0,
        archived=True, comment="some comment",
        previous_prices=[(40.0, 1680000000.0), (45.0, 1685000000.0)],
    )
    original.canonical_name = "Some_Card"
    original.last_date = 1699000000.0
    original.price_is_new = True
    original.quantity_change = -1
    original.previous_quantities = [(3, 1680000000.0)]

    restored = Listing()
    restored.from_json(original.to_json())

    assert restored.seller.name == "alice"
    assert restored.seller.country == "Item location: Germany"
    assert restored.canonical_name == "Some_Card"
    assert restored.language == "English"
    assert restored.condition == "NM"
    assert restored.price == 42.5
    assert restored.quantity == 2
    assert restored.date == 1700000000.0
    assert restored.first_date == 1690000000.0
    assert restored.ended is False
    assert restored.first_ed == 1
    assert restored.reverse_holo == 0
    assert restored.archived is True
    assert restored.comment == "some comment"
    assert restored.price_is_new is True
    assert restored.quantity_change == -1
    assert restored.previous_prices == [(40.0, 1680000000.0), (45.0, 1685000000.0)]
    assert restored.previous_quantities == [(3, 1680000000.0)]


# --- grading -----------------------------------------------------------------

def test_grade_round_trips_through_json():
    original = make_listing(seller="alice", price=800.0, comment="PSA 10",
                            grade_company="PSA", grade=10.0, grade_source="manual")

    restored = Listing()
    restored.from_json(original.to_json())

    assert restored.grade_company == "PSA"
    assert restored.grade == 10.0
    assert restored.grade_source == "manual"
    assert restored.is_graded() is True
    assert restored.grade_label() == "PSA 10"


def test_page_json_without_grading_loads_as_never_inspected():
    """None, not "": a legacy page has not been checked, which is not the same
    as checked-and-ungraded. update_page relies on the difference."""
    restored = Listing()
    restored.from_json({'card': 'x', 'seller': {'name': 'a', 'country': ''}})

    assert restored.grade_company is None
    assert restored.grade is None
    assert restored.grade_source is None
    # Unknown still counts as not graded, so raw prices keep working.
    assert restored.is_graded() is False


def test_apply_parsed_grade_reads_the_comment():
    listing = make_listing(comment="[PSA 10.0] fast shipping", grade_source=None)
    listing.apply_parsed_grade()

    assert (listing.grade_company, listing.grade) == ("PSA", 10.0)
    assert listing.grade_source == "auto"


def test_apply_parsed_grade_leaves_a_manual_grade_alone():
    listing = make_listing(comment="PSA 10", grade_company="BGS", grade=9.5,
                           grade_source="manual")
    listing.apply_parsed_grade()

    assert (listing.grade_company, listing.grade) == ("BGS", 9.5)


def test_build_row_marks_and_classifies_a_slab():
    listing = make_listing(seller="alice", price=800.0, comment="PSA 10",
                           grade_company="PSA", grade=10.0)
    listing.canonical_name = "Test_Card"
    listing.row_number = 0

    html = listing.build_row()

    assert "grade-psa" in html
    assert "gradeval-10" in html
    assert 'data-grade-company="PSA"' in html
    assert ">PSA 10</span>" in html
    assert "edit-grade-btn" in html


def test_build_row_marks_a_raw_listing_as_ungraded():
    listing = make_listing(seller="alice", price=10.0)
    listing.canonical_name = "Test_Card"
    listing.row_number = 0

    html = listing.build_row()

    assert "grade-none" in html
    assert "gradeval-none" in html
    assert "grade-badge" not in html
