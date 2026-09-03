"""Recompute the stored stock counts on every page file.

`available` used to hold raw copies only, so a card whose only listing was a
graded slab reported no stock at all while that listing sat visible underneath.
It now counts every live copy, with `available_graded` carrying the breakdown --
see Page.recompute_available. Existing page files still hold the old figure, and
nothing recomputes it until that card's next import, so this backfills them.

The JSON is patched in place rather than round-tripped through
Page.load_json/save: load_json does not read `sold` or `inserted`, so saving a
loaded page would silently zero the last-import diff.

Usage:
    python -m app.migrate_available            # dry run, reports what would change
    python -m app.migrate_available --apply    # write the new counts
"""

import json
import os
import sys

from app.config import PAGES_DIR, ARCHIVE_DIR


def _live_counts(listings):
    """(total, graded) items across listings that are neither ended nor archived."""
    total = 0
    graded = 0
    for listing in listings:
        if listing.get('ended') or listing.get('archived'):
            continue
        try:
            qty = int(listing.get('quantity') or 0)
        except (TypeError, ValueError):
            qty = 0
        total += qty
        # Mirrors Listing.is_graded: a None company means "never inspected",
        # which is not the same as "checked and found ungraded".
        if listing.get('grade_company'):
            graded += qty
    return total, graded


def migrate(apply_changes=False):
    changed = 0
    unchanged = 0
    errors = 0
    gained_stock = []

    for folder in (PAGES_DIR, ARCHIVE_DIR):
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            if not name.endswith('.json'):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError, OSError) as exc:
                errors += 1
                print(f"Error reading {name}: {exc}")
                continue

            total, graded = _live_counts(data.get('listings', []))
            if data.get('available') == total and data.get('available_graded') == graded:
                unchanged += 1
                continue

            # A card that reported nothing but does have stock is exactly the
            # symptom this migration exists to clear, so call those out.
            if not data.get('available') and total:
                gained_stock.append((name[:-5], total, graded))

            changed += 1
            if apply_changes:
                data['available'] = total
                data['available_graded'] = graded
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                except (IOError, OSError) as exc:
                    errors += 1
                    print(f"Error writing {name}: {exc}")

    print("=== stock recount ===")
    print(f"changed  : {changed}")
    print(f"unchanged: {unchanged}")
    print(f"errors   : {errors}")
    if gained_stock:
        print(f"\ncards that reported zero stock but hold listings ({len(gained_stock)}):")
        for name, total, graded in gained_stock[:20]:
            print(f"  {total:>5} items ({graded} graded)  {name}")
        if len(gained_stock) > 20:
            print(f"  ... and {len(gained_stock) - 20} more")
    if not apply_changes:
        print("\nDry run. Re-run with --apply to write these counts.")


if __name__ == "__main__":
    migrate(apply_changes="--apply" in sys.argv)
