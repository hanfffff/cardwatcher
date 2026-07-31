"""Shared pytest fixtures and helpers for the CardWatcher test suite.

All tests here are pure-logic: no browser, no Flask, no live data directory.
"""
import os

import pytest

from app.listing import Listing, Seller

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture
def sample_page_path():
    return os.path.join(FIXTURES_DIR, "sample_page.json")


@pytest.fixture
def listing_rows_html():
    with open(os.path.join(FIXTURES_DIR, "listing_rows.html"), encoding="utf-8") as f:
        return f.read()


def make_listing(seller="seller", country="Item location: Germany", language="English",
                 condition="NM", price=10.0, quantity=1, date=0.0, ended=False,
                 first_date=0.0, first_ed=0, reverse_holo=0, archived=False,
                 previous_prices=None, comment="",
                 grade_company="", grade=None, grade_source="auto"):
    """Build a Listing with sensible defaults for tests.

    Defaults to an explicitly ungraded listing (grade_company "", source 'auto'),
    i.e. a comment that has been checked and holds no grade -- not the None
    "never looked at" state that only legacy page files carry.
    """
    l = Listing()
    l.seller = Seller()
    l.seller.name = seller
    l.seller.country = country
    l.language = language
    l.condition = condition
    l.price = price
    l.quantity = quantity
    l.date = date
    l.first_date = first_date
    l.ended = ended
    l.first_ed = first_ed
    l.reverse_holo = reverse_holo
    l.archived = archived
    l.comment = comment
    l.previous_prices = previous_prices if previous_prices is not None else []
    l.grade_company = grade_company
    l.grade = grade
    l.grade_source = grade_source
    return l
