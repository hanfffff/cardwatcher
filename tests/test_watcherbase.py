"""Tests for app/watcherbase.py metric calculations."""
import time

from app.watcherbase import watcherbase
from tests.conftest import make_listing


class _FakePage:
    def __init__(self, listings):
        self.listings = listings


# --- calculate_price_average_time_weighted ----------------------------------

def test_time_weighted_empty():
    assert watcherbase.calculate_price_average_time_weighted([]) == 0.0


def test_time_weighted_equal_timestamps_is_mean():
    t = 1700000000.0
    result = watcherbase.calculate_price_average_time_weighted(
        [(10.0, t), (20.0, t)], reference_time=t)
    assert result == 15.0


def test_time_weighted_half_life_decay():
    now = 1700000000.0
    one_half_life_ago = now - 30 * 24 * 60 * 60
    # Recent sale weight 1.0, sale one half-life ago weight 0.5.
    # (10*1 + 20*0.5) / (1 + 0.5) = 20 / 1.5 = 13.333...
    result = watcherbase.calculate_price_average_time_weighted(
        [(10.0, now), (20.0, one_half_life_ago)],
        half_life_days=30, reference_time=now)
    assert result == 13.333333333333334


# --- calculate_historical_min -----------------------------------------------

def test_calculate_historical_min():
    now = time.time()
    day = 24 * 60 * 60
    listings = [
        # existed 30 days ago, active -> counts, price 50
        make_listing(seller="a", price=50.0, first_date=now - 30 * day, date=now),
        # only appeared 2 days ago -> not present at the 7-day cutoff
        make_listing(seller="b", price=5.0, first_date=now - 2 * day, date=now),
        # existed 60 days ago, active -> counts, price 20 (the minimum)
        make_listing(seller="c", price=20.0, first_date=now - 60 * day, date=now),
    ]
    result = watcherbase.calculate_historical_min(_FakePage(listings), days_ago=7)
    assert result == 20.0


def test_calculate_historical_min_no_data():
    assert watcherbase.calculate_historical_min(_FakePage([]), days_ago=7) is None


# --- calculate_market_prices ------------------------------------------------

def test_calculate_market_prices_blend_sold_floor():
    now = time.time()
    active = [make_listing(seller=f"ask{i}", price=p, quantity=1, ended=False)
              for i, p in enumerate([10.0, 11.0, 12.0, 13.0])]
    # Five fresh sales -> recency-weighted W=5 >= BLEND_SOLD_FULL, so the sold
    # side is fully trusted and blend is the plain 0.6/0.4 mix.
    sold = [make_listing(seller=f"sold{i}", price=20.0, quantity=1, ended=True, date=now)
            for i in range(5)]

    result = watcherbase.calculate_market_prices(_FakePage(active + sold))

    assert result["language"] == "English"
    assert result["n_ask"] == 4
    assert result["n_sold"] == 5
    # transaction: time-weighted mean of five 20.0 sales at "now" -> 20.0
    assert result["transaction"] == 20.0
    # floor: 10th percentile of [10,11,12,13] -> 10.3
    assert result["floor"] == 10.3
    # blend: full confidence -> (0.6*20 + 0.4*10.3) = 16.12
    assert result["blend"] == 16.12


def test_blend_leans_to_floor_when_sales_are_sparse():
    # A surging card: live asks have run up (floor ~100) but only a single, older
    # realized sale exists at the old price (20). With W well below BLEND_SOLD_FULL
    # the sold side's weight is largely handed to the floor, so blend sits far
    # above the stale sold price instead of lagging at the old 0.6/0.4 level.
    now = time.time()
    day = 24 * 60 * 60
    active = [make_listing(seller=f"ask{i}", price=p, quantity=1, ended=False)
              for i, p in enumerate([100.0, 102.0, 104.0, 106.0, 108.0])]
    sold = [make_listing(seller="sold0", price=20.0, quantity=1, ended=True,
                         date=now - 20 * day)]  # one old sale -> low confidence

    result = watcherbase.calculate_market_prices(_FakePage(active + sold))

    # One ~20-day-old sale: W = 2^(-20/10) = 0.25, conf = 0.25/4 ~= 0.06.
    # The plain 0.6/0.4 blend would be ~0.6*20 + 0.4*100 = 52; adaptive weighting
    # keeps blend much closer to the live floor.
    assert result["transaction"] == 20.0
    assert result["floor"] >= 100.0
    plain_blend = 0.6 * result["transaction"] + 0.4 * result["floor"]
    assert result["blend"] > plain_blend + 20      # clearly leaning to floor
    assert result["blend"] >= 0.8 * result["floor"]  # and close to it


def test_calculate_market_prices_floor_only_when_no_sales():
    active = [make_listing(seller=f"ask{i}", price=p, quantity=1, ended=False)
              for i, p in enumerate([10.0, 11.0, 12.0, 13.0])]
    result = watcherbase.calculate_market_prices(_FakePage(active))
    assert result["transaction"] == 0.0
    assert result["blend"] == result["floor"] == 10.3


def test_floor_ignores_other_languages():
    # English is the dominant (most-supplied) language and sits ~100. A handful
    # of cheap Japanese asks must not drag the English floor down.
    english = [make_listing(seller=f"en{i}", price=p, quantity=1, ended=False)
               for i, p in enumerate([100.0, 102.0, 104.0, 106.0, 108.0])]
    japanese = [make_listing(seller=f"jp{i}", price=p, quantity=1, ended=False,
                             language="Japanese")
                for i, p in enumerate([55.0, 58.0])]
    result = watcherbase.calculate_market_prices(_FakePage(english + japanese))
    assert result["language"] == "English"
    assert result["floor"] >= 100.0


def test_pinned_language_does_not_fall_back_to_other_languages():
    # When a language is pinned but has no listings in the snapshot, the metric
    # is empty rather than silently borrowing another (cheaper) language.
    japanese = [make_listing(seller=f"jp{i}", price=p, quantity=1, ended=False,
                             language="Japanese")
                for i, p in enumerate([55.0, 58.0, 60.0])]
    result = watcherbase.calculate_market_prices(
        _FakePage(japanese), lang="English")
    assert result["floor"] == 0.0
    assert result["n_ask"] == 0


# --- dominant_language (priority by availability) ---------------------------

def test_dominant_language_prefers_english_even_when_outnumbered():
    # A Western card: English exists, so it wins regardless of Japanese supply.
    listings = ([make_listing(seller=f"jp{i}", language="Japanese")
                 for i in range(10)]
                + [make_listing(seller=f"en{i}", language="English")
                   for i in range(2)])
    assert watcherbase.dominant_language(listings) == "English"


def test_dominant_language_falls_through_to_japanese():
    # No English on offer -> Japanese (Japanese-origin card).
    listings = [make_listing(seller=f"jp{i}", language="Japanese")
                for i in range(4)]
    assert watcherbase.dominant_language(listings) == "Japanese"


def test_dominant_language_chinese_only():
    listings = [make_listing(seller=f"cn{i}", language="S-Chinese")
                for i in range(3)]
    assert watcherbase.dominant_language(listings) == "S-Chinese"


def test_dominant_language_ignores_single_stray():
    # One mislabeled English listing must not hijack a Japanese-only product.
    listings = ([make_listing(seller=f"jp{i}", language="Japanese")
                 for i in range(6)]
                + [make_listing(seller="stray", language="English")])
    assert watcherbase.dominant_language(listings) == "Japanese"


def test_dominant_language_non_priority_falls_back_to_supply():
    # Only Western non-English languages present -> most-supplied wins.
    listings = ([make_listing(seller=f"de{i}", language="German")
                 for i in range(3)]
                + [make_listing(seller="fr", language="French")])
    assert watcherbase.dominant_language(listings) == "German"


# --- calculate_all_period_averages (shape) ----------------------------------

def test_calculate_all_period_averages_shape():
    now = time.time()
    day = 24 * 60 * 60
    listings = [
        make_listing(seller="a", price=10.0, quantity=2, ended=False,
                     first_date=now - 200 * day, date=now),
        make_listing(seller="b", price=12.0, quantity=1, ended=False,
                     first_date=now - 200 * day, date=now),
        make_listing(seller="c", price=15.0, quantity=1, ended=True,
                     first_date=now - 200 * day, date=now - 10 * day),
    ]
    result = watcherbase.calculate_all_period_averages(_FakePage(listings))

    for key in ("current_avg", "current_ended_avg", "current_available",
                "current_min", "market"):
        assert key in result
    assert result["current_available"] == 3  # qty 2 + 1 active

    for period in ("1w", "1m", "2m", "6m"):
        assert period in result
        assert "market" in result[period]
        assert "blend" in result[period]["market"]


# --- raw vs graded split ------------------------------------------------------

def _mixed_page():
    """A card with cheap raw copies and expensive slabs, all English NM."""
    now = time.time()
    return _FakePage([
        make_listing(seller="a", price=40.0, quantity=2, date=now, first_date=now - 86400),
        make_listing(seller="b", price=45.0, quantity=1, date=now, first_date=now - 86400),
        make_listing(seller="c", price=50.0, quantity=1, date=now, first_date=now - 86400),
        make_listing(seller="d", price=55.0, quantity=1, date=now, first_date=now - 86400),
        make_listing(seller="e", price=800.0, quantity=1, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="f", price=850.0, quantity=3, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="g", price=300.0, quantity=1, date=now, first_date=now - 86400,
                     grade_company="BGS", grade=9.5),
    ])


def test_graded_asks_do_not_move_the_raw_floor():
    page = _mixed_page()

    raw = watcherbase.calculate_market_prices(page)
    mixed = watcherbase.calculate_market_prices(page, grade=None)

    assert raw['basis'] == 'raw'
    assert raw['n_ask'] == 4                 # slabs excluded
    assert raw['floor'] <= 55.0              # priced off the raw copies alone
    assert mixed['floor'] > raw['floor']     # the old, polluted number


def test_graded_only_card_falls_back_and_says_so():
    """Better an honestly labelled graded price than a silent zero."""
    now = time.time()
    page = _FakePage([
        make_listing(seller="a", price=800.0, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=10.0),
    ])

    market = watcherbase.calculate_market_prices(page)

    assert market['basis'] == 'all'
    assert market['floor'] == 800.0


def _thin_expensive_raw_page():
    """A dried-up card: two odd, dear raw asks under a stack of cheaper slabs."""
    now = time.time()
    day = 86400
    return _FakePage([
        # What is left "raw" is sealed/bundled product, priced way above the slabs.
        make_listing(seller="a", price=1200.0, date=now, first_date=now - 30 * day),
        make_listing(seller="b", price=1500.0, date=now, first_date=now - 30 * day),
        make_listing(seller="c", price=300.0, date=now, first_date=now - 30 * day,
                     grade_company="PSA", grade=9.0),
        make_listing(seller="d", price=320.0, date=now, first_date=now - 30 * day,
                     grade_company="BGS", grade=9.0),
        make_listing(seller="e", price=800.0, date=now, first_date=now - 30 * day,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="f", price=850.0, date=now, first_date=now - 30 * day,
                     grade_company="PSA", grade=10.0),
    ])


def test_thin_expensive_raw_pool_falls_back_to_the_graded_floor():
    market = watcherbase.calculate_market_prices(_thin_expensive_raw_page())

    # Not the ~1200 nobody can buy a single for: the cheapest 9 on the page.
    assert market['floor'] == 300.0
    assert market['basis'] == 'graded-floor'
    assert market['n_ask'] == 2                  # still reports the raw sample


def test_graded_floor_fallback_ignores_low_grades():
    """A PSA 4 is worth less than a raw NM copy, so it may not set the floor."""
    now = time.time()
    page = _FakePage([
        make_listing(seller="a", price=1200.0, date=now, first_date=now - 86400),
        make_listing(seller="b", price=60.0, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=4.0),
        make_listing(seller="c", price=70.0, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=5.0),
        make_listing(seller="d", price=80.0, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=6.0),
    ])

    market = watcherbase.calculate_market_prices(page)

    assert market['basis'] == 'raw'
    assert market['floor'] == 1200.0


def test_healthy_raw_pool_keeps_its_own_floor():
    """The normal case is untouched: plenty of raw asks, slabs dearer than raw."""
    market = watcherbase.calculate_market_prices(_mixed_page())

    assert market['basis'] == 'raw'
    assert market['floor'] <= 55.0


def test_graded_floor_fallback_needs_more_than_one_slab():
    """One cheap slab is an anecdote, not a market to reprice the card off."""
    now = time.time()
    page = _FakePage([
        make_listing(seller="a", price=1200.0, date=now, first_date=now - 86400),
        make_listing(seller="b", price=300.0, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=10.0),
    ])

    market = watcherbase.calculate_market_prices(page)

    assert market['basis'] == 'raw'
    assert market['floor'] == 1200.0


def test_graded_floor_fallback_stays_in_the_raw_market_language():
    """Substituting a Japanese slab for an English raw floor crosses markets."""
    now = time.time()
    page = _FakePage([
        make_listing(seller="a", price=1200.0, date=now, first_date=now - 86400),
        make_listing(seller="b", price=1500.0, date=now, first_date=now - 86400),
    ] + [
        make_listing(seller=f"jp{i}", language="Japanese", price=300.0, date=now,
                     first_date=now - 86400, grade_company="PSA", grade=9.0)
        for i in range(3)
    ])

    market = watcherbase.calculate_market_prices(page)

    assert market['language'] == "English"
    assert market['basis'] == 'raw'
    assert market['floor'] == 1230.0        # the English asks' own low band


def test_no_raw_asks_at_all_takes_the_graded_floor():
    """The extreme case: nothing raw on offer, so report a slab, not a zero."""
    now = time.time()
    page = _FakePage([
        # A realized raw sale keeps the card on the raw basis (nothing raw live).
        make_listing(seller="a", price=900.0, quantity=0, date=now - 86400,
                     first_date=now - 200000, ended=True),
    ] + [
        make_listing(seller=f"g{i}", price=p, date=now, first_date=now - 86400,
                     grade_company="PSA", grade=9.0)
        for i, p in enumerate([300.0, 320.0, 340.0])
    ])

    market = watcherbase.calculate_market_prices(page)

    assert market['basis'] == 'graded-floor'
    assert market['floor'] == 300.0
    assert market['transaction'] == 900.0        # raw sales are left alone


def test_graded_premium_is_not_reported_off_a_substituted_floor():
    """Slab over slab is ~1x, which would read as "grading adds nothing"."""
    page = _thin_expensive_raw_page()

    assert watcherbase.calculate_market_prices(page)['basis'] == 'graded-floor'
    assert watcherbase.calculate_graded_premium(page) == 0.0


def test_graded_buckets_split_by_company_and_grade():
    page = _mixed_page()

    buckets = watcherbase.calculate_graded_buckets(page)

    assert set(buckets) == {"PSA 10", "BGS 9.5"}
    # Floor is the plain minimum ask: a two-listing pool has no meaningful
    # percentile band.
    assert buckets["PSA 10"]['floor'] == 800.0
    assert buckets["PSA 10"]['n_ask'] == 2
    assert buckets["PSA 10"]['available'] == 4      # quantities, not listings
    assert buckets["BGS 9.5"]['floor'] == 300.0


def test_graded_buckets_report_realized_prices():
    now = time.time()
    page = _FakePage([
        make_listing(seller="a", price=900.0, quantity=0, date=now - 86400,
                     first_date=now - 200000, ended=True,
                     grade_company="PSA", grade=10.0),
        make_listing(seller="b", price=880.0, quantity=0, date=now - 172800,
                     first_date=now - 300000, ended=True,
                     grade_company="PSA", grade=10.0),
    ])

    bucket = watcherbase.calculate_graded_buckets(page)["PSA 10"]

    assert bucket['n_sold'] == 2
    assert bucket['last_sold'] == 900.0             # newest first
    assert [price for price, _ in bucket['sold']] == [900.0, 880.0]


def test_graded_premium_is_the_cheapest_top_grade_over_raw():
    page = _mixed_page()

    premium = watcherbase.calculate_graded_premium(page)

    raw_floor = watcherbase.calculate_market_prices(page)['floor']
    # Top grade on offer is 10; cheapest ask at that grade is 800.
    assert premium == round(800.0 / raw_floor, 2)


def test_graded_premium_needs_a_slab_in_the_raw_market_language():
    """Comparing an English raw floor to a Japanese slab is not a premium."""
    now = time.time()
    page = _FakePage([
        make_listing(seller="a", price=40.0, date=now, first_date=now - 86400),
        make_listing(seller="b", price=45.0, date=now, first_date=now - 86400),
        make_listing(seller="c", language="Japanese", price=800.0, date=now,
                     first_date=now - 86400, grade_company="PSA", grade=10.0),
    ])

    assert watcherbase.calculate_graded_premium(page) == 0.0


def test_period_metrics_report_graded_alongside_raw():
    page = _mixed_page()

    metrics = watcherbase.calculate_all_period_averages(page)

    assert metrics['current_available'] == 5        # raw quantities only
    assert metrics['current_available_graded'] == 5
    assert set(metrics['graded']) == {"PSA 10", "BGS 9.5"}
    assert metrics['graded_premium'] > 1
    # The headline raw numbers must not see the slabs.
    assert metrics['current_min'] == 40.0
    assert metrics['current_avg'] < 100
