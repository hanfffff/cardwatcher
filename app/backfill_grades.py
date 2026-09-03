"""Populate grading data on pages saved before grading support existed.

Reads the grade out of every listing's comment and stores it, then recomputes
the metrics in price_history.json -- which will move, because graded copies stop
counting towards the raw price and supply of a card.

Runs as a dry run by default: it touches nothing and prints what it would do,
plus the comments the parser was unsure about. That is deliberate. This rewrites
every page and every stored metric, and the parser reads free text written by
strangers, so it is worth a look before it is worth trusting.

Usage:
    python -m app.backfill_grades              # report only, writes nothing
    python -m app.backfill_grades --apply      # actually save
"""

import argparse
import collections
import os

from app.config import ARCHIVE_DIR, PAGES_DIR
from app.grading_libraries import parse_grade
from app.page import Page
from app.watcherbase import watcherbase


def _page_files():
    """Every page on disk, active and archived, as (directory, filename)."""
    for folder in (PAGES_DIR, ARCHIVE_DIR):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if name.endswith(".json"):
                yield folder, name


def backfill(apply_changes=False, review_limit=40):
    tally = collections.Counter()
    review = collections.Counter()
    pages_changed = 0
    listings_graded = 0
    listings_seen = 0
    kept_manual = 0
    errors = 0

    for folder, name in _page_files():
        canonical_name = name[:-5]
        try:
            page = Page()
            page.canonical_name = canonical_name
            page.import_page(os.path.join(folder, name))

            changed = False
            for listing in page.listings:
                listings_seen += 1
                if listing.grade_source == 'manual':
                    # A correction the user made by hand. Never overwrite it.
                    kept_manual += 1
                    continue

                before = (listing.grade_company, listing.grade)
                company, grade, needs_review = parse_grade(listing.comment)
                if needs_review:
                    review[listing.comment] += 1
                if (company, grade) != before or listing.grade_source != 'auto':
                    changed = True
                listing.grade_company = company
                listing.grade = grade
                listing.grade_source = 'auto'
                if company:
                    tally[company + " " + str(grade)] += 1
                    listings_graded += 1

            if changed:
                pages_changed += 1
            if changed and apply_changes:
                # Recompute the stock split, which save() persists.
                page.recompute_available()
                page.save()
                watcherbase.update_price_history_for_page(page)

        except Exception as exc:
            errors += 1
            print(f"Error processing {name}: {exc}")

    print("=== grades found ===")
    for label, count in tally.most_common():
        print("%6d  %s" % (count, label))

    print("\n=== needs review (%d distinct comments) ===" % len(review))
    print("The parser was unsure about these. Nothing is wrong with them")
    print("necessarily -- an unnamed grader or two grades in one comment is")
    print("enough to land here. Correct any that are wrong with the pencil on")
    print("the card page; a manual grade is never overwritten by a later import.")
    for comment, count in review.most_common(review_limit):
        company, grade, _ = parse_grade(comment)
        print("  %4d [%s] %s" % (count, company + " " + str(grade) if company else "ungraded",
                                 comment[:100]))
    if len(review) > review_limit:
        print("  ... and %d more" % (len(review) - review_limit))

    print("\n=== summary ===")
    print("listings inspected : %d" % listings_seen)
    print("listings graded    : %d" % listings_graded)
    print("manual grades kept : %d" % kept_manual)
    print("pages to update    : %d" % pages_changed)
    print("errors             : %d" % errors)
    if apply_changes:
        print("\nSaved. Pages and price_history.json have been rewritten.")
    else:
        print("\nDry run -- nothing was written. Re-run with --apply to save.")

    return pages_changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the changes (default is a dry run)")
    parser.add_argument("--review-limit", type=int, default=40,
                        help="how many flagged comments to print (default 40)")
    args = parser.parse_args()
    backfill(apply_changes=args.apply, review_limit=args.review_limit)


if __name__ == "__main__":
    main()
