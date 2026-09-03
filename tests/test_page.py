"""Tests for app/page.py — robust averaging and update_page matching logic."""
from app.page import Page
from tests.conftest import make_listing


# --- calculate_price_average_robust (IQR) -----------------------------------

def test_robust_average_empty():
    assert Page.calculate_price_average_robust([]) == 0.0


def test_robust_average_small_set_is_simple_mean():
    # Fewer than 4 points -> plain mean, no filtering.
    assert Page.calculate_price_average_robust([10.0, 20.0, 30.0]) == 20.0


def test_robust_average_zero_iqr_is_simple_mean():
    # All-equal quartiles -> IQR 0 -> falls back to plain mean (outlier kept).
    assert Page.calculate_price_average_robust([10, 10, 10, 10, 1000]) == 208.0


def test_robust_average_filters_outlier():
    # IQR>0: the 1000 sits outside the upper bound and is dropped.
    assert Page.calculate_price_average_robust([10, 12, 14, 16, 1000]) == 13.0


# --- update_page matching: continuing / relisted / ended --------------------

def _make_new_page(canonical, listings, price_average):
    page = Page()
    page.canonical_name = canonical
    page.card = "Card"
    page.set = "Set"
    page.image = "img.jpg"
    page.languages = ["English"]
    page.only_germany = False
    page.loadMoreButton = False
    page.listings = listings
    page.price_average = price_average
    return page


def test_update_page_continuing_relisted_and_ended():
    canonical = "Test_Card"

    # Existing (old) state.
    old = Page()
    old.canonical_name = canonical
    old.price_average = 10.0
    alice_old = make_listing(seller="alice", price=10.0, date=100.0, first_date=100.0)
    bob_old = make_listing(seller="bob", price=20.0, date=100.0, first_date=100.0)
    carol_old = make_listing(seller="carol", price=30.0, date=80.0, first_date=50.0,
                             ended=True)
    old.listings = [alice_old, bob_old, carol_old]

    # Incoming (new) data: alice's price changed, carol relisted, bob gone.
    alice_new = make_listing(seller="alice", price=12.0, date=200.0, first_date=200.0,
                             quantity=1)
    carol_new = make_listing(seller="carol", price=33.0, date=200.0, first_date=200.0,
                             quantity=1, comment="back again")
    new = _make_new_page(canonical, [alice_new, carol_new], price_average=15.0)

    old.update_page(new)

    by_seller = {l.seller.name: l for l in old.listings}

    # alice: continuing with a price change
    alice = by_seller["alice"]
    assert alice.new is False
    assert alice.ended is False
    assert alice.price == 12.0
    assert alice.price_is_new is True
    assert (10.0, 100.0) in alice.previous_prices
    assert alice.first_date == 100.0  # original first_date preserved

    # carol: relisted (was ended, reappeared)
    carol = by_seller["carol"]
    assert carol.new is False
    assert carol.ended is False
    assert carol.comment.startswith("RELISTED!")

    # bob: gone -> ended and counted as sold
    bob = by_seller["bob"]
    assert bob.ended is True

    assert old.sold == 1
    assert old.inserted == 1  # carol re-counted; alice was continuing
    assert old.price_change == 5.0  # 15.0 - 10.0
    assert old.price_average == 15.0


def test_update_page_marks_unchanged_listing_not_new():
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.listings = [make_listing(seller="alice", price=10.0, date=100.0, first_date=100.0)]

    alice_new = make_listing(seller="alice", price=10.0, date=200.0, first_date=200.0)
    new = _make_new_page(canonical, [alice_new], price_average=10.0)

    old.update_page(new)

    assert len(old.listings) == 1
    assert old.listings[0].new is False
    assert old.listings[0].price_is_new is False
    assert old.sold == 0
    assert old.inserted == 0


# --- update_page matching: grading ------------------------------------------

def test_grade_separates_two_slabs_from_one_seller():
    """A seller's PSA 10 and PSA 9 are two listings, not one flip-flopping one.

    Without grade in the match key these collapse into a single tracked listing
    whose price history jumps between two unrelated price levels.
    """
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.listings = [
        make_listing(seller="alice", price=800.0, date=100.0, first_date=100.0,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="alice", price=300.0, date=100.0, first_date=100.0,
                     grade_company="PSA", grade=9.0),
    ]

    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=850.0, date=200.0, first_date=200.0,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="alice", price=310.0, date=200.0, first_date=200.0,
                     grade_company="PSA", grade=9.0),
    ], price_average=0.0)

    old.update_page(new)

    by_grade = {l.grade: l for l in old.listings}
    assert len(old.listings) == 2
    # Each kept its own history rather than being matched to the other.
    assert by_grade[10.0].price == 850.0
    assert by_grade[10.0].previous_prices == [(800.0, 100.0)]
    assert by_grade[9.0].price == 310.0
    assert by_grade[9.0].previous_prices == [(300.0, 100.0)]
    assert old.sold == 0


def test_legacy_listing_without_grade_still_matches():
    """Pages saved before grading support must not all end and re-list at once."""
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    legacy = make_listing(seller="alice", price=10.0, date=100.0, first_date=100.0)
    legacy.grade_company = None       # never inspected
    legacy.grade = None
    legacy.grade_source = None
    old.listings = [legacy]

    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=10.0, date=200.0, first_date=200.0,
                     grade_company="PSA", grade=10.0),
    ], price_average=10.0)

    old.update_page(new)

    assert len(old.listings) == 1
    assert old.listings[0].new is False       # matched, not treated as new
    assert old.listings[0].first_date == 100.0
    assert old.sold == 0
    # The freshly parsed grade is adopted.
    assert old.listings[0].grade_company == "PSA"


def test_manual_grade_survives_reimport():
    """A hand-set grade outranks what the next import parses from the comment."""
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.listings = [make_listing(seller="alice", price=10.0, date=100.0, first_date=100.0,
                                 comment="Don't expect PSA10",
                                 grade_company="PSA", grade=9.0, grade_source="manual")]

    # The importer parsed the comment differently (here: as ungraded).
    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=10.0, date=200.0, first_date=200.0,
                     comment="Don't expect PSA10"),
    ], price_average=10.0)

    old.update_page(new)

    assert len(old.listings) == 1
    listing = old.listings[0]
    assert listing.new is False
    assert (listing.grade_company, listing.grade) == ("PSA", 9.0)
    assert listing.grade_source == "manual"


def test_available_counts_graded_and_reports_the_split():
    """Stock is every copy on offer; the graded share is reported next to it.

    A slab is still a copy a buyer can buy, so leaving it out made a card whose
    only listing was graded read as zero stock while that listing sat visible
    below it. The raw/graded split that matters for *pricing* is enforced
    separately, by watcherbase._excluded_from_raw.
    """
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.listings = []

    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=10.0, quantity=3, date=200.0, first_date=200.0),
        make_listing(seller="bob", price=800.0, quantity=2, date=200.0, first_date=200.0,
                     grade_company="PSA", grade=10.0),
    ], price_average=10.0)

    old.update_page(new)

    assert old.available == 5          # 3 raw + 2 slabs
    assert old.available_graded == 2


def test_build_grading_selection_lists_only_present_grades():
    page = Page()
    page.listings = [
        make_listing(seller="alice", grade_company="PSA", grade=10.0),
        make_listing(seller="bob", grade_company="BGS", grade=9.5),
        make_listing(seller="carol"),
    ]

    html = page.build_grading_selection()

    assert 'value="grade-psa"' in html
    assert 'value="grade-bgs"' in html
    assert 'value="grade-none"' in html          # raw copies exist
    assert 'value="gradeval-10"' in html
    assert 'value="gradeval-9-5"' in html        # dot is not CSS-safe
    assert 'value="grade-cgc"' not in html       # not on this card


def test_build_grading_selection_empty_without_slabs():
    page = Page()
    page.listings = [make_listing(seller="alice")]
    assert page.build_grading_selection() == ""


def test_set_listing_grade_marks_it_manual(tmp_path, monkeypatch):
    import app.page as page_module
    monkeypatch.setattr(page_module, "PAGES_DIR", str(tmp_path))
    monkeypatch.setattr(page_module, "ARCHIVE_DIR", str(tmp_path))

    page = Page()
    page.canonical_name = "Test_Card"
    page.listings = [make_listing(seller="alice")]

    assert page.set_listing_grade(0, "PSA", 10.0) is True
    assert (page.listings[0].grade_company, page.listings[0].grade) == ("PSA", 10.0)
    assert page.listings[0].grade_source == "manual"

    # Clearing it is how a false positive gets killed.
    assert page.set_listing_grade(0, "", None) is True
    assert page.listings[0].is_graded() is False
    assert page.listings[0].grade_source == "manual"

    assert page.set_listing_grade(99, "PSA", 10.0) is False


# --- supply accounting: items, not rows -------------------------------------

def test_inserted_and_sold_count_items_not_rows():
    """A row of N copies is N items of supply, not one.

    Counting rows made the gallery badge print a stock figure in items next to a
    change figure in rows -- "700" beside "+300" on a first import, where 300 was
    simply CardMarket's listing cap.
    """
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.listings = []

    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=10.0, quantity=40, date=200.0, first_date=200.0),
        make_listing(seller="bob", price=11.0, quantity=7, date=200.0, first_date=200.0),
    ], price_average=10.0)

    old.update_page(new)

    assert old.available == 47
    assert old.inserted == 47          # items, not the 2 rows
    assert old.sold == 0


def test_sold_counts_the_copies_an_ended_listing_still_held():
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.price_average = 10.0
    old.listings = [
        make_listing(seller="alice", price=10.0, quantity=5, date=100.0, first_date=100.0),
        make_listing(seller="bob", price=20.0, quantity=3, date=100.0, first_date=100.0),
    ]

    # bob is gone entirely; alice unchanged.
    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=10.0, quantity=5, date=200.0, first_date=200.0),
    ], price_average=10.0)

    old.update_page(new)

    assert old.sold == 3               # bob's 3 copies, not "1 listing"
    assert old.inserted == 0
    assert old.available == 5


def test_partial_quantity_change_is_item_flow():
    """A seller going 10 -> 6 sold 4 items even though the row still stands.

    Without this the identity available == previous + inserted - sold breaks the
    moment a seller sells part of a stack, which is the common case.
    """
    canonical = "Test_Card"
    old = Page()
    old.canonical_name = canonical
    old.price_average = 10.0
    old.listings = [
        make_listing(seller="alice", price=10.0, quantity=10, date=100.0, first_date=100.0),
        make_listing(seller="bob", price=12.0, quantity=2, date=100.0, first_date=100.0),
    ]
    previous_available = 12

    new = _make_new_page(canonical, [
        make_listing(seller="alice", price=10.0, quantity=6, date=200.0, first_date=200.0),
        make_listing(seller="bob", price=12.0, quantity=5, date=200.0, first_date=200.0),
    ], price_average=10.0)

    old.update_page(new)

    assert old.sold == 4               # alice's 10 -> 6
    assert old.inserted == 3           # bob's 2 -> 5
    assert old.available == 11
    assert old.available == previous_available + old.inserted - old.sold


def test_archiving_a_listing_updates_stock():
    """Archiving used to leave the stored count untouched until the next import."""
    page = Page()
    page.canonical_name = "Test_Card"
    page.listings = [
        make_listing(seller="alice", price=10.0, quantity=4),
        make_listing(seller="bob", price=12.0, quantity=6),
    ]
    page.recompute_available()
    assert page.available == 10

    page.save = lambda: None           # keep the test off disk
    page.archive_listing(1)
    assert page.available == 4

    page.unarchive_listing(1)
    assert page.available == 10
