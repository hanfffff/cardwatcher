"""Tests for comment -> grade parsing.

Every string here is a real comment harvested from cardwatcher-data/pages, not
an invented example. The parser exists to survive what sellers actually write,
so the corpus is the specification.
"""
import pytest

from app.grading_libraries import (
    UNKNOWN_COMPANY,
    format_grade,
    grade_slug,
    normalize_grade,
    parse_grade,
)


# (comment, expected company, expected grade)
GRADED = [
    # The plain forms, in every casing and spacing sellers use.
    ("PSA 10", "PSA", 10.0),
    ("PSA10", "PSA", 10.0),
    ("psa 10", "PSA", 10.0),
    ("Psa10", "PSA", 10.0),
    ("BGS 9.5", "BGS", 9.5),
    ("bgs 9.5", "BGS", 9.5),
    ("CGC 8.5", "CGC", 8.5),
    ("AOG 10", "AOG", 10.0),
    ("SGC 10 UNLIMITED", "SGC", 10.0),
    ("Tag 10", "TAG", 10.0),
    ("ARS 10 with certificate", "ARS", 10.0),
    # Beckett is BGS.
    ("Beckett 9.5", "BGS", 9.5),
    ("[BECKETT 8.5] Pruckis Cards TCG Store Berlin", "BGS", 8.5),
    # The bracketed marker some listing tools emit, including zero padding.
    ("[PSA 10.0]", "PSA", 10.0),
    ("[PSA 10.0] Graded Card - Store in Udine", "PSA", 10.0),
    ("### BGS-Scan: 0018029786 [BGS 8.5] (50716)", "BGS", 8.5),
    ("### CGC-Scan: 6055941004 [CGC 08] (50751)", "CGC", 8.0),
    ("### PSA-Scan: 132460062 [PSA 09] (50477)", "PSA", 9.0),
    # Decoration around the grade.
    ("! PSA 10 !", "PSA", 10.0),
    ("!!! PSA 9 !!! 12-05/26/1 1024/87", "PSA", 9.0),
    ("_PSA 10_ Since 1996 - TOP SERVICE", "PSA", 10.0),
    ("< PGS 9.5 > # G2 # -> fast and secure shipping", "PGS", 9.5),
    ("-1§ PSA 10", "PSA", 10.0),
    ("1§  CGC 5.5 6137544114", "CGC", 5.5),
    ("[ PSA 10 ] #1604", "PSA", 10.0),
    # Separators other than a space.
    ("[PSA 9.0] PSA - 9.0", "PSA", 9.0),
    ("BOOSTERFRISCH ;toploader PGS 9;5", "PGS", 9.5),
    # Spelled-out labels between company and number.
    ("CGC Pristine 10", "CGC", 10.0),
    ("CGC GRADE PRISTINE 10", "CGC", 10.0),
    ("PSA GRADING 10 NICE CARD!!", "PSA", 10.0),
    ("ACE Graded 10", "ACE", 10.0),
    ("PGS Grading 10", "PGS", 10.0),
    ("Aigrading 9.5", "AIG", 9.5),
    ("[AiGrad 9.5] Graded Card - Store in Udine", "AIG", 9.5),
    # Label after the number.
    ("PSA 10 GEM MINT", "PSA", 10.0),
    ("BGS 10 Pristine", "BGS", 10.0),
    ("1 of 1 AOG 10 GEM MINT | DM for offer", "AOG", 10.0),
    # The relist prefix the importer prepends must not hide the grade.
    ("RELISTED! PSA 10", "PSA", 10.0),
    ("RELISTED! [PSA 10.0]", "PSA", 10.0),
    ("RELISTED! PSA10", "PSA", 10.0),
    # Trailing full stop is punctuation, not a decimal point.
    ("PSA 10. ask for pictures", "PSA", 10.0),
    ("PSA10. PM for photo", "PSA", 10.0),
    ("Psa 10. mint. fast ship!", "PSA", 10.0),
    ("PSA 10. inkl. 19% MwSt", "PSA", 10.0),
    # A cert number next to the grade must not be read as the grade.
    ("*MORE PSA ON MY ACCOUNT* PSA 10 - 159642249 - GRD-0021", "PSA", 10.0),
    ("[PSA 9.0] 112803286", "PSA", 9.0),
    ("* PSA 9 / 092500025", "PSA", 9.0),
    ("--> CGC 9 MINT  A0892", "CGC", 9.0),
    ("[BGS 9.0] 05202627 toploader", "BGS", 9.0),
    # Subgrades listed after the headline grade.
    ("1132/5000 BGS10 SUB: 9.5 10 10 10 ask for more", "BGS", 10.0),
    ("[BGS 9.0] (CEN 10/COR 8.5/EDG 9/ SUR 9) - SICK DEALS", "BGS", 9.0),
    ("!!PICS IN DM!! BGS 9.5 / Centering 9 - Edges 9.5 - Corners 10 - Surface 10", "BGS", 9.5),
    # Sales patter that must not suppress a real grade.
    ("1st ED. PSA 10 Gem Mint - Check our Page 100s off Graded/Raw Cards", "PSA", 10.0),
    ("- - PSA 8 - - FREE SHIPPING - -check out our other cards graded by CGC and PSA", "PSA", 8.0),
    ("PSA 10. check Profile for more Tournament Cards", "PSA", 10.0),
    ("PSA 9 - Fast shipping. contact for questions/pics", "PSA", 9.0),
    # Number before company.
    ("9.5 PGS", "PGS", 9.5),
]


UNGRADED = [
    # No grading mentioned at all.
    "",
    "Fast shipping with tracking",
    "Umsatzsteuerbefreit gem. § 6 Abs. 1 Z 27 UStG",
    "18th place 26/05 -> Fast shipping (toploader)",
    # Marketing about the seller's own grading standards, not a slab.
    "Strict grading_Fair price_Supreme service",
    "Strict grading | Daily shipping | Safely packed!",
    "_*HUGE STOCK AND FAIR GRADING*_ _*Daily Shipments - Top Packaging*_",
    "Full Stock! Fast Shipping! Strict Grading! Tienda en Madrid Centro",
    "super clean. perfect for grading",
    "some whiten on bottom. good for collection not grading.",
    "Gradable",
    # A grader named without a grade: the seller stocks slabs, this is not one.
    "Many Japanese Vintage Singles&Graded cards available!",
    "De #1 dealer in ACE & TAG slabs van de Benelux!",
    "@ACE",
    "verified by AOG with Mint + estimated condition",
    # Grades the graders do not issue.
    "PSA 9.7",
    # Speculation about a grade the card does not have.
    "Don't expect PSA10",
    "DON'T EXPECT A PSA10",
    "Potential psa10",
    "Possible psa 10",
    "TRUE CONDITION / PERFECT FRONT - PERFECT BACK / POTENTIAL PSA 10 /RELIABLE SELLER",
    "#489 >> pot. PSA 10 / BGS10",
    "RAW | Maybe PSA9 - PSA 10",
    "Sealed promo (potential psa10) + with art book!",
    "Check out my other chinese cards!.possible psa 10",
    "1/ TRUE CONDITION / PERFECT FRONT - PERFECT BACK / POTENTIAL PSA 10 /RELIABLE SELLER",
]


@pytest.mark.parametrize("comment,company,grade", GRADED)
def test_parses_real_graded_comments(comment, company, grade):
    assert parse_grade(comment)[:2] == (company, grade)


@pytest.mark.parametrize("comment", UNGRADED)
def test_leaves_ungraded_comments_alone(comment):
    company, grade, _ = parse_grade(comment)
    assert company == ""
    assert grade is None


def test_speculation_is_flagged_for_review():
    """Suppressed grades are reported, so the backfill can show them."""
    assert parse_grade("Don't expect PSA10") == ("", None, True)
    # A comment with no grading at all is not worth flagging.
    assert parse_grade("Fast shipping") == ("", None, False)


def test_grade_candidates_are_raw_wherever_the_word_sits():
    """"PSA 10 candidate" is a raw card being talked up, not a slab."""
    for comment in [
        "PSA 10 candidate",
        "psa10 candidate, fresh from pack",
        "Candidate PSA 10",
        "Mint condition - BGS 9.5 candidate - fast shipping",
        "PSA-Kandidat 10",
        "[PSA 10] candidate",          # even the authoritative bracketed marker
    ]:
        company, grade, _ = parse_grade(comment)
        assert (company, grade) == ("", None), comment


def test_candidate_without_a_grade_is_not_flagged():
    assert parse_grade("Great centering, grading candidate") == ("", None, False)
    assert parse_grade("PSA 10 candidate") == ("", None, True)


def test_conflicting_grades_take_the_first_and_flag():
    company, grade, needs_review = parse_grade(
        "SET OF 2 - PSA9 + PSA8 - Fast/safe bubblewrap shipping")
    assert (company, grade) == ("PSA", 9.0)
    assert needs_review


def test_repeated_agreeing_grades_are_not_flagged():
    assert parse_grade("[PSA 10.0] PSA10") == ("PSA", 10.0, False)
    assert parse_grade("[BGS 10.0] safe and fast shipping. BGS 10") == ("BGS", 10.0, False)


def test_unnamed_grader_is_captured_but_flagged():
    """A slab with no grader named still must not count as a raw card."""
    assert parse_grade("Graded 9.5") == (UNKNOWN_COMPANY, 9.5, True)
    assert parse_grade("Grad 10") == (UNKNOWN_COMPANY, 10.0, True)
    assert parse_grade("AP Grading 8.5") == (UNKNOWN_COMPANY, 8.5, True)


def test_black_label_implies_ten():
    """BGS only awards the label to a 10, so the number is left out."""
    assert parse_grade("BGS Black Label") == ("BGS", 10.0, True)
    assert parse_grade("BGS BLACK LABEL") == ("BGS", 10.0, True)


def test_normalize_grade_accepts_seller_decimal_separators():
    assert normalize_grade("09") == 9.0
    assert normalize_grade("9,5") == 9.5
    # The legacy text page format wrote commas out as semicolons.
    assert normalize_grade("9;5") == 9.5


def test_format_grade_drops_the_trailing_zero():
    assert format_grade("PSA", 10.0) == "PSA 10"
    assert format_grade("BGS", 9.5) == "BGS 9.5"
    assert format_grade("", None) == ""


def test_grade_slug_is_css_safe():
    assert grade_slug(10.0) == "10"
    assert grade_slug(9.5) == "9-5"
    assert grade_slug(None) == "none"
