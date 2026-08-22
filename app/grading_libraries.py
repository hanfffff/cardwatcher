"""Grading-company lookups and comment parsing.

CardMarket has no structured field for professional grading: a seller with a
slabbed card writes it into the free-text comment. In practice that means a
company token next to a number, wrapped in whatever decoration the seller's
listing tool produces -- ``PSA 10``, ``[BGS 9.5]``, ``CGC Pristine 10``,
``_PSA 10_ Since 1996``, ``< PGS 9.5 >``, ``1132/5000 BGS10 SUB: 9.5 10 10 10``.

``parse_grade`` turns that into a (company, grade) pair. It is deliberately
conservative: a comment that mentions a grader without an adjacent number, or
that speculates about one, yields no grade. Everything it is unsure about comes
back flagged so the backfill can show it rather than guess.
"""

import re


# Canonical company code per alias. Beckett slabs are labelled BGS (and BVG for
# the vintage line); AiGrad shortens to AIG. Everything else is already its own
# canonical form.
GRADING_COMPANIES = {
    "PSA": "PSA",
    "BGS": "BGS",
    "BECKETT": "BGS",
    "BVG": "BGS",
    "CGC": "CGC",
    "AOG": "AOG",
    "SGC": "SGC",
    "ACE": "ACE",
    "TAG": "TAG",
    "GMA": "GMA",
    "PGS": "PGS",
    "AIGRADING": "AIG",
    "AIGRAD": "AIG",
    "AIG": "AIG",
    "ARS": "ARS",
}

# A slab whose grader we could not name. Worth capturing rather than dropping:
# an unattributed "Graded 9.5" is still a slab, and leaving it in the raw pool is
# exactly the price pollution this feature exists to remove. Always flagged for
# review so it can be corrected to a real company by hand.
UNKNOWN_COMPANY = "UNK"

# Display order for filter UIs: by how common they are in practice, so the
# checkboxes a user actually wants sit at the top of the list.
COMPANY_ORDER = ["PSA", "BGS", "CGC", "AOG", "ACE", "PGS", "SGC", "AIG", "ARS",
                 "GMA", "TAG", UNKNOWN_COMPANY]

# Badge colour per company, roughly matching each grader's slab label so the
# row markers are recognisable at a glance.
COMPANY_COLORS = {
    "PSA": "#c8102e",
    "BGS": "#1c3f94",
    "CGC": "#00a3e0",
    "AOG": "#6a3d9a",
    "ACE": "#0f8a5f",
    "PGS": "#b8860b",
    "SGC": "#333333",
    "AIG": "#8a6d3b",
    "ARS": "#a4243b",
    "GMA": "#7a7a7a",
    "TAG": "#005f73",
    UNKNOWN_COMPANY: "#6c757d",
}

# Label for the filter UI; only the unattributed bucket needs spelling out.
COMPANY_LABELS = {UNKNOWN_COMPANY: "Graded (unknown)"}

# Longest alias first so AIGRAD wins over AIG.
_COMPANY_ALT = "|".join(sorted(GRADING_COMPANIES, key=len, reverse=True))

# Separators between the company and the number: whitespace, dashes (including
# the unicode ones sellers paste in), colons and dots. ``PSA - 9.0``, ``PSA10``,
# ``PSA: 10`` and ``PSA. 10`` are all the same thing.
_SEP = r"[\s\-‐-―:.]*"

# Sellers often spell the label out between company and number. These are
# descriptions of the grade, not part of it, so they are skipped. ``GRAD*``
# covers "PSA GRADING 10", "ACE Graded 10", "PGS Grading 10".
_DESCRIPTOR = (r"(?:GEM\s*[\s\-]?\s*(?:MINT|MT)|BLACK\s*LABEL|PRISTINE"
               r"|GRAD(?:ING|ED|E)|MINT|MT)")

# 10 / 10.0, or 1-9 with an optional .0/.5, or the zero-padded 08 / 09 form some
# listing tools emit. ``;`` is accepted as a decimal separator because the legacy
# text page format replaced commas with semicolons on save.
_NUMBER = r"(10(?:[.,;]0)?|0?[1-9](?:[.,;][05])?)"

# What may NOT follow a grade. Another digit rules out cert numbers: in
# "### PSA-Scan: 132460051 [PSA 10]" those digits cannot match without leaving
# one behind, so only the bracketed [PSA 10] is picked up. A separator followed
# by a digit rules out half-grades the graders do not issue ("9.7" is not a
# grade, so it is rejected rather than silently read as 9). A separator followed
# by anything else is just punctuation, which is why "PSA 10. ask for pics"
# still parses -- that trailing full stop ends a sentence, it is not a decimal.
_NOT_FOLLOWED = r"(?!\d)(?![.,;]\d)"

# The leading lookbehind excludes letters and digits but deliberately allows
# "_", which sellers use purely as decoration (``_PSA 10_ Since 1996``).
# The descriptor repeats because sellers stack them: "CGC GRADE PRISTINE 10".
GRADE_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + _COMPANY_ALT + r")"
    + _SEP
    + r"(?:" + _DESCRIPTOR + r"[\s\-:.]*)*"
    + _NUMBER
    + _NOT_FOLLOWED,
    re.IGNORECASE,
)

# The rarer "9.5 PGS" ordering. Only consulted when the normal form finds
# nothing, so a comment like "BGS 9.5 ... 10 PSA cards in stock" is not misread.
GRADE_RE_REVERSED = re.compile(
    r"(?<![\d.,;])" + _NUMBER + r"\s*(" + _COMPANY_ALT + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)

# "Graded 9.5", "Grad 10", "GRADING 8" -- a slab with no grader named. Last
# resort, consulted only when no company could be identified at all.
BARE_GRADE_RE = re.compile(
    r"(?<![A-Za-z0-9])GRAD(?:ING|ED|E)?" + r"[\s\-:.]*" + _NUMBER + _NOT_FOLLOWED,
    re.IGNORECASE,
)

# "BGS Black Label" carries no number because it does not need one: the label is
# only awarded to a 10 with straight-10 subgrades. These are the most valuable
# slabs there are, so leaving them in the raw pool would skew a card badly.
BLACK_LABEL_RE = re.compile(
    r"(?<![A-Za-z0-9])(" + _COMPANY_ALT + r")" + _SEP + r"BLACK\s*LABEL",
    re.IGNORECASE,
)

# Speculation, not a fact: "POTENTIAL PSA 10", "Don't expect PSA10",
# "#489 >> pot. PSA 10". Checked only against the text shortly BEFORE a match --
# a blanket search of the whole comment would throw away real grades, because
# graded listings are full of unrelated sales patter ("PSA 10 - Check our page",
# "*MORE PSA ON MY ACCOUNT* PSA 10 - 159642249").
NEGATION_RE = re.compile(
    r"(?<![A-Za-z])(potential(?:ly)?|pot\.|possib(?:le|ly)|maybe|expect(?:ed|ing)?"
    r"|worthy|evtl\.?|eventuell|k[oö]nnte|vielleicht)(?![A-Za-z])",
    re.IGNORECASE,
)

# Speculation that is NOT tied to a position. Sellers write "PSA 10 candidate"
# (and the German "PSA-Kandidat") just as readily after the grade as before it,
# so the NEGATION_WINDOW lookbehind would miss exactly the common form. The word
# only ever means "raw card I hope would grade this", never a slab, so its mere
# presence disqualifies the whole comment.
BLANKET_NEGATION_RE = re.compile(
    r"(?<![A-Za-z])(candidate|kandidat)", re.IGNORECASE,
)

# How far back from a match to look for that speculation. Wide enough to cover
# "pot. PSA 10 / BGS10" (where the qualifier governs both grades), short enough
# that an unrelated earlier sentence cannot suppress a real grade.
NEGATION_WINDOW = 20

# page.update_page() prepends this when a dead listing comes back, so it sits in
# front of the seller's own text and has to come off before matching.
RELISTED_PREFIX = "RELISTED! "


def normalize_company(token):
    """Canonical company code for a raw token, or None if not a known grader."""
    return GRADING_COMPANIES.get(token.strip().upper())


def normalize_grade(token):
    """Grade token -> float. Handles ``09``, ``9,5``, ``9;5`` and ``10.0``."""
    return float(token.replace(",", ".").replace(";", "."))


def format_grade_number(grade):
    """The bare number without its trailing zero: 10.0 -> ``10``, 9.5 -> ``9.5``."""
    if grade is None:
        return ""
    return ("%.1f" % grade).rstrip("0").rstrip(".")


def format_grade(company, grade):
    """Human label for a grade, e.g. ``PSA 10`` / ``BGS 9.5``. '' when not graded."""
    if not company or grade is None:
        return ""
    return company + " " + format_grade_number(grade)


def grade_slug(grade):
    """CSS-safe form of a grade number: 9.5 -> ``9-5``, 10.0 -> ``10``."""
    if grade is None:
        return "none"
    return format_grade_number(grade).replace(".", "-")


def _negated(text, start):
    """True when the text just before ``start`` speculates about the grade."""
    return bool(NEGATION_RE.search(text[max(0, start - NEGATION_WINDOW):start]))


def _mentions_grade(text):
    """True when any grade pattern appears, trusted or not.

    Only used to decide whether a suppressed comment is worth flagging: a
    comment with no grading in it at all is not review-worthy.
    """
    return bool(GRADE_RE.search(text) or GRADE_RE_REVERSED.search(text)
                or BLACK_LABEL_RE.search(text) or BARE_GRADE_RE.search(text))


def parse_grade(comment):
    """Extract the professional grade a seller wrote into their comment.

    Returns ``(company, grade, needs_review)``:

    - ``("PSA", 10.0, False)`` -- graded, unambiguously.
    - ``("", None, False)``    -- no grading mentioned at all.
    - ``("", None, True)``     -- a grade was mentioned but not trusted (only
      speculative mentions), so it is reported as ungraded and flagged.
    - ``("PSA", 10.0, True)``  -- several different grades in one comment; the
      first is used (the bracketed leading marker sellers' tools emit is the
      authoritative one) and the conflict is flagged.
    - ``("UNK", 9.5, True)``   -- graded, grader not named ("Graded 9.5").

    ``needs_review`` never changes the stored value. It exists so the backfill
    can print the handful of comments worth a human glance.
    """
    if not comment:
        return "", None, False

    text = comment
    if text.startswith(RELISTED_PREFIX):
        text = text[len(RELISTED_PREFIX):]

    # "PSA 10 candidate" is a raw card the seller is talking up, wherever in the
    # comment the word sits -- see BLANKET_NEGATION_RE.
    if BLANKET_NEGATION_RE.search(text):
        return "", None, _mentions_grade(text)

    found = []
    suppressed = False

    for match in GRADE_RE.finditer(text):
        company = normalize_company(match.group(1))
        if company is None:
            continue
        if _negated(text, match.start()):
            suppressed = True
            continue
        found.append((company, normalize_grade(match.group(2))))

    if not found and not suppressed:
        for match in GRADE_RE_REVERSED.finditer(text):
            company = normalize_company(match.group(2))
            if company is None:
                continue
            if _negated(text, match.start()):
                suppressed = True
                continue
            found.append((company, normalize_grade(match.group(1))))

    if not found and not suppressed:
        for match in BLACK_LABEL_RE.finditer(text):
            company = normalize_company(match.group(1))
            if company is None or _negated(text, match.start()):
                continue
            # Flagged because the grade is inferred from the label, not written.
            return company, 10.0, True

    if not found and not suppressed:
        for match in BARE_GRADE_RE.finditer(text):
            if _negated(text, match.start()):
                suppressed = True
                continue
            # Never confident about who graded it, so always worth a look.
            return UNKNOWN_COMPANY, normalize_grade(match.group(1)), True

    if not found:
        return "", None, suppressed

    company, grade = found[0]
    conflicting = any(entry != found[0] for entry in found)
    return company, grade, conflicting
