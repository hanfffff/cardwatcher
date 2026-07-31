"""
Collection management for CardWatcher.
Handles personal card collection with price calculation.
"""
import os
import json
import time
from datetime import datetime, date, timedelta
from app.config import COLLECTION_FILE
from app.page import Page


class CollectionItem:
    """Represents a single card in the user's collection."""

    def __init__(self, canonical_name, condition="NM", language="English",
                 first_ed=0, reverse_holo=0, quantity=1, added_at=None,
                 grade_company="", grade=None):
        self.canonical_name = canonical_name
        self.condition = condition
        self.language = language
        self.first_ed = first_ed  # 0=no (unchecked), 2=yes (checked)
        self.reverse_holo = reverse_holo  # 0=no (unchecked), 2=yes (checked)
        self.quantity = quantity
        self.added_at = added_at or time.time()
        self.grade_company = grade_company  # "" = raw, "PSA"/"BGS"/... = slabbed
        self.grade = grade                  # float or None

    def identity(self):
        """The attribute tuple that makes this entry distinct in the collection.

        Grade is part of it because a PSA 10 and a raw copy of one card are two
        different holdings worth wildly different amounts -- merging them would
        make the collection value meaningless.
        """
        return (self.canonical_name, self.condition, self.language,
                self.first_ed, self.reverse_holo, self.grade_company, self.grade)

    def to_dict(self):
        return {
            "canonical_name": self.canonical_name,
            "condition": self.condition,
            "language": self.language,
            "first_ed": self.first_ed,
            "reverse_holo": self.reverse_holo,
            "quantity": self.quantity,
            "added_at": self.added_at,
            "grade_company": self.grade_company,
            "grade": self.grade
        }

    @staticmethod
    def from_dict(data):
        # Preserve added_at from saved data - don't let it default to time.time()
        # when loading from disk
        item = CollectionItem.__new__(CollectionItem)
        item.canonical_name = data.get("canonical_name", "")
        item.condition = data.get("condition", "NM")
        item.language = data.get("language", "English")
        item.first_ed = data.get("first_ed", 0)
        item.reverse_holo = data.get("reverse_holo", 0)
        item.quantity = data.get("quantity", 1)
        item.added_at = data.get("added_at")  # Can be None for legacy items
        # Entries saved before grading support are raw: unlike a listing, a
        # collection entry was typed in by the user, so there is nothing
        # un-inspected about it.
        item.grade_company = data.get("grade_company", "") or ""
        grade = data.get("grade")
        item.grade = float(grade) if grade is not None else None
        return item


def identity_of(canonical_name, condition, language, first_ed, reverse_holo,
                grade_company="", grade=None):
    """Build the same tuple CollectionItem.identity() returns, for lookups."""
    return (canonical_name, condition, language, first_ed, reverse_holo,
            grade_company or "", grade)


class Collection:
    """Manages the user's card collection."""

    def __init__(self):
        self.items = []
        self.updated_at = None
        # Acquisition history: tracks when items were added
        self.value_history = {}  # {date_str: {total, items, cards}}
        self.history_backfilled_to = None  # Earliest date we've backfilled to
        # Portfolio history: treats all current items as always owned
        self.portfolio_history = {}  # {date_str: {total, items, cards}}
        self.portfolio_backfilled_to = None

    def load(self):
        """Load collection from JSON file."""
        if os.path.exists(COLLECTION_FILE):
            try:
                with open(COLLECTION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.items = [CollectionItem.from_dict(item) for item in data.get("items", [])]
                    self.updated_at = data.get("updated_at")
                    self.value_history = data.get("value_history", {})
                    self.history_backfilled_to = data.get("history_backfilled_to")
                    self.portfolio_history = data.get("portfolio_history", {})
                    self.portfolio_backfilled_to = data.get("portfolio_backfilled_to")
            except (json.JSONDecodeError, IOError):
                self.items = []
                self.updated_at = None
                self.value_history = {}
                self.history_backfilled_to = None
                self.portfolio_history = {}
                self.portfolio_backfilled_to = None
        return self

    def save(self):
        """Save collection to JSON file."""
        self.updated_at = time.time()
        data = {
            "items": [item.to_dict() for item in self.items],
            "updated_at": self.updated_at,
            "value_history": self.value_history,
            "portfolio_history": self.portfolio_history,
        }
        if self.history_backfilled_to:
            data["history_backfilled_to"] = self.history_backfilled_to
        if self.portfolio_backfilled_to:
            data["portfolio_backfilled_to"] = self.portfolio_backfilled_to
        with open(COLLECTION_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def add_item(self, canonical_name, condition="NM", language="English",
                 first_ed=0, reverse_holo=0, quantity=1, added_at=None,
                 grade_company="", grade=None):
        """Add or update an item in the collection."""
        # Invalidate acquisition history if item has older added_at
        if added_at:
            self._invalidate_history_from_date(added_at)

        # Always invalidate portfolio history (item is now "always owned")
        self._invalidate_portfolio_history()

        target = identity_of(canonical_name, condition, language, first_ed,
                             reverse_holo, grade_company, grade)

        # Check if item with same attributes exists
        for item in self.items:
            if item.identity() == target:
                # Update quantity
                item.quantity += quantity
                # Keep the earlier added_at date if a new one is provided
                if added_at and (not item.added_at or added_at < item.added_at):
                    item.added_at = added_at
                self.save()
                return item

        # Add new item
        item = CollectionItem(
            canonical_name=canonical_name,
            condition=condition,
            language=language,
            first_ed=first_ed,
            reverse_holo=reverse_holo,
            quantity=quantity,
            added_at=added_at,
            grade_company=grade_company or "",
            grade=grade
        )
        self.items.append(item)
        self.save()
        return item

    def _invalidate_history_from_date(self, timestamp):
        """
        Remove cached value_history entries from the given timestamp onwards.
        Called when an item is added with an older added_at date.
        """
        if not self.value_history:
            return

        item_date = date.fromtimestamp(timestamp)
        item_date_str = item_date.isoformat()

        # Remove all cached values from this date onwards
        dates_to_remove = [
            d for d in self.value_history.keys()
            if d >= item_date_str
        ]

        for d in dates_to_remove:
            del self.value_history[d]

        # Reset backfill marker if we're invalidating history before it
        if self.history_backfilled_to and item_date_str <= self.history_backfilled_to:
            self.history_backfilled_to = None

    def _invalidate_portfolio_history(self):
        """
        Clear all portfolio history. Called when items are added/removed
        since portfolio mode treats all items as always owned.
        """
        self.portfolio_history = {}
        self.portfolio_backfilled_to = None

    def update_item(self, canonical_name, condition, language, first_ed, reverse_holo,
                    quantity, grade_company="", grade=None):
        """Update quantity of an existing item."""
        target = identity_of(canonical_name, condition, language, first_ed,
                             reverse_holo, grade_company, grade)
        for item in self.items:
            if item.identity() == target:
                if quantity <= 0:
                    self.items.remove(item)
                    self._invalidate_portfolio_history()
                else:
                    item.quantity = quantity
                    self._invalidate_portfolio_history()
                self.save()
                return True
        return False

    def edit_item(self, canonical_name, old_condition, old_language, old_first_ed, old_reverse_holo,
                  new_condition, new_language, new_first_ed, new_reverse_holo, new_quantity,
                  new_added_at=None, old_grade_company="", old_grade=None,
                  new_grade_company="", new_grade=None):
        """
        Edit an item's attributes. Since attributes are the item's identity,
        this removes the old item and creates a new one.
        """
        old_target = identity_of(canonical_name, old_condition, old_language,
                                 old_first_ed, old_reverse_holo, old_grade_company, old_grade)
        new_target = identity_of(canonical_name, new_condition, new_language,
                                 new_first_ed, new_reverse_holo, new_grade_company, new_grade)

        # Find the old item
        old_item = None
        for item in self.items:
            if item.identity() == old_target:
                old_item = item
                break

        if not old_item:
            return False

        # Preserve added_at if not provided
        if new_added_at is None:
            new_added_at = old_item.added_at

        # Check if new attributes match an existing item (other than the old one)
        for item in self.items:
            if item == old_item:
                continue
            if item.identity() == new_target:
                # Merge into existing item
                item.quantity += new_quantity
                if new_added_at and (not item.added_at or new_added_at < item.added_at):
                    item.added_at = new_added_at
                self.items.remove(old_item)
                self._invalidate_portfolio_history()
                if new_added_at:
                    self._invalidate_history_from_date(new_added_at)
                self.save()
                return True

        # Update the item in place
        old_item.condition = new_condition
        old_item.language = new_language
        old_item.first_ed = new_first_ed
        old_item.reverse_holo = new_reverse_holo
        old_item.quantity = new_quantity
        old_item.added_at = new_added_at
        old_item.grade_company = new_grade_company or ""
        old_item.grade = new_grade

        self._invalidate_portfolio_history()
        if new_added_at:
            self._invalidate_history_from_date(new_added_at)
        self.save()
        return True

    def remove_item(self, canonical_name, condition=None, language=None,
                    first_ed=None, reverse_holo=None, grade_company="", grade=None):
        """Remove item(s) from collection."""
        removed = []
        target = identity_of(canonical_name, condition, language, first_ed,
                             reverse_holo, grade_company, grade)
        for item in self.items[:]:
            if item.canonical_name != canonical_name:
                continue
            # If no filters specified, remove all entries for this card
            if condition is None:
                self.items.remove(item)
                removed.append(item)
            # Otherwise, match specific attributes
            elif item.identity() == target:
                self.items.remove(item)
                removed.append(item)

        if removed:
            self._invalidate_portfolio_history()
            self.save()
        return removed

    def get_items_for_page(self, canonical_name):
        """Get all collection items for a specific page/card."""
        return [item for item in self.items if item.canonical_name == canonical_name]

    def get_canonical_names(self):
        """Get set of all canonical names in collection."""
        return set(item.canonical_name for item in self.items)

    def to_dict(self):
        """Convert collection to dictionary."""
        result = {
            "items": [item.to_dict() for item in self.items],
            "updated_at": self.updated_at,
            "value_history": self.value_history,
            "portfolio_history": self.portfolio_history,
        }
        if self.history_backfilled_to:
            result["history_backfilled_to"] = self.history_backfilled_to
        if self.portfolio_backfilled_to:
            result["portfolio_backfilled_to"] = self.portfolio_backfilled_to
        return result


# How far back a sold listing still counts as a price signal. Raw supply turns
# over constantly; graded supply does not (see calculate_collection_price).
RAW_ENDED_WINDOW_DAYS = 7
GRADED_ENDED_WINDOW_DAYS = 90


def _grade_matches(listing, grade_company, grade):
    """Whether a listing is the same product as a held item, grading-wise.

    Exact both ways. A PSA 10 must never be priced off a raw copy (it is worth a
    multiple of it), and a raw copy must never be priced off a slab. This is
    enforced even in the relaxed passes below, which loosen condition and
    first_ed but never this.
    """
    if grade_company:
        return listing.grade_company == grade_company and listing.grade == grade
    return not listing.is_graded()


def calculate_collection_price(page, condition, language, first_ed, reverse_holo,
                               grade_company="", grade=None):
    """
    Calculate realistic market price for a card with specific attributes.

    Priority order:
    1. Lowest available listing with exact match (condition, language, first_ed, reverse_holo)
    2. Latest ended listing with exact match
    3. Relaxed match: ignore first_ed, allow condition one grade better
    4. Overall average of available listings

    Args:
        page: Page object with listings
        condition: Card condition (MT, NM, EX, GD, LP, PL, PO)
        language: Card language
        first_ed: First edition flag (0=no/unchecked, 2=yes/checked)
        reverse_holo: Reverse holo flag (0=no/unchecked, 2=yes/checked)
        grade_company: Grading company ("" for a raw card)
        grade: Numeric grade, or None for a raw card

    Returns:
        float: Calculated price, or 0 if no data
    """
    # Exclude archived listings, and anything that is not the same product as
    # what is held: pricing a slab off raw listings (or vice versa) is the single
    # biggest way this number can be wrong.
    available = [l for l in page.listings if not l.ended and not l.archived
                 and _grade_matches(l, grade_company, grade)]
    # Only consider recently ended listings, to avoid misleading old prices. The
    # window is far wider for slabs: a card has dozens of raw listings turning
    # over constantly, but often a single PSA 10 that sells once a quarter, and a
    # 7-day window would leave nearly every graded holding priced at 0.
    window_days = GRADED_ENDED_WINDOW_DAYS if grade_company else RAW_ENDED_WINDOW_DAYS
    cutoff = time.time() - window_days * 24 * 3600
    ended = [l for l in page.listings if l.ended and not l.archived
             and l.date and float(l.date) >= cutoff
             and _grade_matches(l, grade_company, grade)]

    # Condition grades from best to worst
    condition_grades = ['MT', 'NM', 'EX', 'GD', 'LP', 'PL', 'PO']

    def is_condition_same_or_one_better(listing_condition, target_condition):
        """Check if listing condition is same or one grade better than target."""
        try:
            listing_idx = condition_grades.index(listing_condition)
            target_idx = condition_grades.index(target_condition)
            # Listing can be same grade or one better (lower index = better)
            return listing_idx >= target_idx - 1 and listing_idx <= target_idx
        except ValueError:
            return listing_condition == target_condition

    def matches(listing):
        """Check if listing matches the specified attributes."""
        if listing.condition != condition:
            return False
        if listing.language != language:
            return False
        # For first_ed and reverse_holo:
        # Collection/UI convention: 0=unchecked (not first ed), 2=checked (is first ed)
        # Listing convention: 0=no, 1=yes, 2=unknown
        # When collection checkbox is checked (2), match listing "yes" (1)
        # When collection checkbox is unchecked (0), match listing "no" (0)
        if first_ed == 2 and listing.first_ed != 1:  # Want first ed, listing must be first ed
            return False
        if first_ed == 0 and listing.first_ed != 0:  # Want non-first ed, listing must be non-first ed
            return False
        if reverse_holo == 2 and listing.reverse_holo != 1:  # Want reverse holo, listing must be reverse holo
            return False
        if reverse_holo == 0 and listing.reverse_holo != 0:  # Want non-reverse holo, listing must be non-reverse holo
            return False
        return True

    def matches_relaxed(listing):
        """Relaxed match: ignore first_ed, allow condition one grade better."""
        if not is_condition_same_or_one_better(listing.condition, condition):
            return False
        if listing.language != language:
            return False
        # Ignore first_ed in relaxed matching
        # Still check reverse_holo
        if reverse_holo == 2 and listing.reverse_holo != 1:
            return False
        if reverse_holo == 0 and listing.reverse_holo != 0:
            return False
        return True

    # 1. Exact match in available listings - use lowest price
    exact_available = [l for l in available if matches(l)]
    if exact_available:
        return min(l.price for l in exact_available)

    # 2. Exact match in ended listings - use most recent
    exact_ended = [l for l in ended if matches(l)]
    if exact_ended:
        latest = max(exact_ended, key=lambda l: l.last_date or l.date or 0)
        return latest.price

    # 3. Relaxed match: ignore first_ed, allow condition one grade better
    relaxed_available = [l for l in available if matches_relaxed(l)]
    if relaxed_available:
        return min(l.price for l in relaxed_available)

    # 4. Fallback to average of all available
    if available:
        return sum(l.price for l in available) / len(available)

    # 4. No data
    return 0


def calculate_collection_value(collection, pages_cache=None):
    """
    Calculate total value of the collection.

    Args:
        collection: Collection object
        pages_cache: Optional dict of {canonical_name: Page} to avoid reloading

    Returns:
        tuple: (total_value, item_count, items_with_prices)
            items_with_prices is list of (item, price) tuples
    """
    from app.config import PAGES_DIR, ARCHIVE_DIR

    total_value = 0.0
    item_count = 0
    items_with_prices = []

    pages_cache = pages_cache or {}

    for item in collection.items:
        # Load page if not in cache
        if item.canonical_name not in pages_cache:
            page = Page()
            # Try pages dir first, then archive
            page_path = os.path.join(PAGES_DIR, item.canonical_name + ".json")
            if not os.path.exists(page_path):
                page_path = os.path.join(ARCHIVE_DIR, item.canonical_name + ".json")
            if os.path.exists(page_path):
                page.import_page(page_path)
                pages_cache[item.canonical_name] = page
            else:
                pages_cache[item.canonical_name] = None

        page = pages_cache.get(item.canonical_name)
        if page is None:
            items_with_prices.append((item, 0))
            continue

        price = calculate_collection_price(
            page,
            item.condition,
            item.language,
            item.first_ed,
            item.reverse_holo,
            item.grade_company,
            item.grade
        )

        item_value = price * item.quantity
        total_value += item_value
        item_count += item.quantity
        items_with_prices.append((item, price))

    return total_value, item_count, items_with_prices


def calculate_historical_collection_price(page, condition, language, first_ed, reverse_holo,
                                          target_time, grade_company="", grade=None):
    """
    Calculate collection price for a card at a specific historical point in time.

    Similar to calculate_collection_price but:
    - Only considers listings that existed at target_time
    - Uses historical prices from listing.previous_prices via get_price_at_time()

    Args:
        page: Page object with listings
        condition: Card condition (MT, NM, EX, GD, LP, PL, PO)
        language: Card language
        first_ed: First edition flag (0=no/unchecked, 2=yes/checked)
        reverse_holo: Reverse holo flag (0=no/unchecked, 2=yes/checked)
        target_time: Unix timestamp for the historical point
        grade_company: Grading company ("" for a raw card)
        grade: Numeric grade, or None for a raw card

    Returns:
        float: Calculated price at that time, or 0 if no data
    """
    from app.watcherbase import watcherbase

    # Condition grades from best to worst
    condition_grades = ['MT', 'NM', 'EX', 'GD', 'LP', 'PL', 'PO']

    def is_condition_same_or_one_better(listing_condition, target_condition):
        """Check if listing condition is same or one grade better than target."""
        try:
            listing_idx = condition_grades.index(listing_condition)
            target_idx = condition_grades.index(target_condition)
            return listing_idx >= target_idx - 1 and listing_idx <= target_idx
        except ValueError:
            return listing_condition == target_condition

    def listing_existed_at_time(listing, t):
        """Check if listing was available at time t."""
        try:
            first_date = float(listing.first_date) if listing.first_date else 0
        except (ValueError, TypeError):
            first_date = 0

        # Listing must have existed by target time
        if first_date <= 0 or first_date > t:
            return False

        # Check if it was still active at that time
        if listing.ended:
            try:
                last_seen = float(listing.date) if listing.date else 0
            except (ValueError, TypeError):
                last_seen = 0
            # If last seen before target time, it wasn't available
            if last_seen < t:
                return False

        return True

    def matches(listing):
        """Check if listing matches the specified attributes."""
        if listing.condition != condition:
            return False
        if listing.language != language:
            return False
        if first_ed == 2 and listing.first_ed != 1:
            return False
        if first_ed == 0 and listing.first_ed != 0:
            return False
        if reverse_holo == 2 and listing.reverse_holo != 1:
            return False
        if reverse_holo == 0 and listing.reverse_holo != 0:
            return False
        return True

    def matches_relaxed(listing):
        """Relaxed match: ignore first_ed, allow condition one grade better."""
        if not is_condition_same_or_one_better(listing.condition, condition):
            return False
        if listing.language != language:
            return False
        if reverse_holo == 2 and listing.reverse_holo != 1:
            return False
        if reverse_holo == 0 and listing.reverse_holo != 0:
            return False
        return True

    # Filter to listings that existed at target_time (excluding archived, and
    # anything of a different grading -- same rule as calculate_collection_price)
    graded_like = [l for l in page.listings if _grade_matches(l, grade_company, grade)]
    available_at_time = [l for l in graded_like if listing_existed_at_time(l, target_time) and not l.ended and not l.archived]
    # For ended listings, check if they were available at target_time (ended after target)
    ended_at_time = []
    for l in graded_like:
        if l.ended and not l.archived:
            try:
                last_seen = float(l.date) if l.date else 0
                first_date = float(l.first_date) if l.first_date else 0
            except (ValueError, TypeError):
                continue
            # Was available at target_time if it started before and ended after
            if first_date <= target_time <= last_seen:
                available_at_time.append(l)
            # For ended fallback: was it active but ended by target_time?
            elif first_date <= target_time and last_seen <= target_time:
                ended_at_time.append(l)

    # 1. Exact match in available listings at that time - use lowest historical price
    exact_available = [l for l in available_at_time if matches(l)]
    if exact_available:
        prices = [watcherbase.get_price_at_time(l, target_time) for l in exact_available]
        return min(prices)

    # 2. Exact match in ended listings at that time - use most recent
    exact_ended = [l for l in ended_at_time if matches(l)]
    if exact_ended:
        latest = max(exact_ended, key=lambda l: l.date or 0)
        return watcherbase.get_price_at_time(latest, target_time)

    # 3. Relaxed match
    relaxed_available = [l for l in available_at_time if matches_relaxed(l)]
    if relaxed_available:
        prices = [watcherbase.get_price_at_time(l, target_time) for l in relaxed_available]
        return min(prices)

    # 4. Fallback to average of all available at that time
    if available_at_time:
        prices = [watcherbase.get_price_at_time(l, target_time) for l in available_at_time]
        return sum(prices) / len(prices)

    return 0


def calculate_historical_collection_value(collection, target_date, pages_cache=None, mode='acquisition'):
    """
    Calculate total collection value at a specific historical date.

    Args:
        collection: Collection object
        target_date: date object for the historical point
        pages_cache: Optional dict of {canonical_name: Page} to avoid reloading
        mode: 'acquisition' (respects added_at dates) or 'portfolio' (all items always owned)

    Returns:
        tuple: (total_value, item_count, card_count)
    """
    from app.config import PAGES_DIR, ARCHIVE_DIR

    # Convert date to end-of-day timestamp
    target_datetime = datetime.combine(target_date, datetime.max.time())
    target_time = target_datetime.timestamp()

    total_value = 0.0
    item_count = 0
    card_count = 0

    pages_cache = pages_cache or {}

    for item in collection.items:
        # In acquisition mode, skip items that weren't in collection yet at target date
        # In portfolio mode, include all items regardless of added_at
        if mode == 'acquisition' and item.added_at and item.added_at > target_time:
            continue

        # Load page if not in cache
        if item.canonical_name not in pages_cache:
            page = Page()
            page_path = os.path.join(PAGES_DIR, item.canonical_name + ".json")
            if not os.path.exists(page_path):
                page_path = os.path.join(ARCHIVE_DIR, item.canonical_name + ".json")
            if os.path.exists(page_path):
                page.import_page(page_path)
                pages_cache[item.canonical_name] = page
            else:
                pages_cache[item.canonical_name] = None

        page = pages_cache.get(item.canonical_name)
        if page is None:
            continue

        price = calculate_historical_collection_price(
            page,
            item.condition,
            item.language,
            item.first_ed,
            item.reverse_holo,
            target_time,
            item.grade_company,
            item.grade
        )

        item_value = price * item.quantity
        total_value += item_value
        item_count += item.quantity
        card_count += 1

    return total_value, item_count, card_count


def load_pages_for_collection(collection):
    """
    Load all pages needed for collection calculations.

    Args:
        collection: Collection object

    Returns:
        dict: {canonical_name: Page or None}
    """
    from app.config import PAGES_DIR, ARCHIVE_DIR

    pages_cache = {}
    for item in collection.items:
        if item.canonical_name not in pages_cache:
            page = Page()
            page_path = os.path.join(PAGES_DIR, item.canonical_name + ".json")
            if not os.path.exists(page_path):
                page_path = os.path.join(ARCHIVE_DIR, item.canonical_name + ".json")
            if os.path.exists(page_path):
                page.import_page(page_path)
                pages_cache[item.canonical_name] = page
            else:
                pages_cache[item.canonical_name] = None
    return pages_cache


def update_value_history(collection, pages_cache=None, mode='acquisition'):
    """
    Add today's value to history if not already present.

    Args:
        collection: Collection object (will be modified and saved if updated)
        pages_cache: Optional preloaded pages
        mode: 'acquisition' or 'portfolio'

    Returns:
        bool: True if new entry was added
    """
    today_str = date.today().isoformat()

    # Select the appropriate history dict
    history = collection.value_history if mode == 'acquisition' else collection.portfolio_history

    # Already have today's value
    if today_str in history:
        return False

    # Calculate current value
    pages_cache = pages_cache or load_pages_for_collection(collection)
    total_value, item_count, items_with_prices = calculate_collection_value(collection, pages_cache)
    card_count = len(set(item.canonical_name for item in collection.items))

    # Store today's value
    history[today_str] = {
        "total": round(total_value, 2),
        "items": item_count,
        "cards": card_count
    }

    collection.save()
    return True


def get_value_history(collection, period_days=60, mode='acquisition'):
    """
    Get value history data for graphing.

    Args:
        collection: Collection object (must be loaded)
        period_days: Number of days of history to return
        mode: 'acquisition' or 'portfolio'

    Returns:
        list of tuples: [(date_str, value), ...] sorted by date ascending
    """
    # Select the appropriate history dict
    history_dict = collection.value_history if mode == 'acquisition' else collection.portfolio_history

    if not history_dict:
        return []

    # Get all dates in range
    today = date.today()
    start_date = today - timedelta(days=period_days)

    history = []
    for date_str, data in history_dict.items():
        try:
            d = date.fromisoformat(date_str)
            if start_date <= d <= today:
                total = data.get("total", 0) if isinstance(data, dict) else data
                history.append((date_str, total))
        except (ValueError, TypeError):
            continue

    # Sort by date
    history.sort(key=lambda x: x[0])
    return history


def backfill_value_history(collection, max_days=180, pages_cache=None, mode='acquisition'):
    """
    Calculate and store historical values for dates not yet in history.
    Only runs once per mode - tracked by history_backfilled_to / portfolio_backfilled_to.

    Args:
        collection: Collection object
        max_days: Maximum days to backfill
        pages_cache: Optional preloaded pages
        mode: 'acquisition' or 'portfolio'

    Returns:
        int: Number of days backfilled
    """
    # Select appropriate history dict and backfill marker
    if mode == 'acquisition':
        history = collection.value_history
        backfilled_to = collection.history_backfilled_to
    else:
        history = collection.portfolio_history
        backfilled_to = collection.portfolio_backfilled_to

    # Check if already backfilled far enough
    today = date.today()
    target_start = today - timedelta(days=max_days)

    if backfilled_to:
        try:
            backfilled_date = date.fromisoformat(backfilled_to)
            if backfilled_date <= target_start:
                return 0  # Already backfilled far enough
        except (ValueError, TypeError):
            pass

    # Load pages once for all calculations
    pages_cache = pages_cache or load_pages_for_collection(collection)

    # For acquisition mode, find earliest item added_at
    # For portfolio mode, we can go back to target_start
    if mode == 'acquisition':
        earliest_item_date = today
        for item in collection.items:
            if item.added_at:
                try:
                    item_date = date.fromtimestamp(item.added_at)
                    if item_date < earliest_item_date:
                        earliest_item_date = item_date
                except (ValueError, OSError):
                    pass
        actual_start = max(target_start, earliest_item_date)
    else:
        # Portfolio mode: backfill the full range
        actual_start = target_start

    # Calculate for each day
    days_backfilled = 0
    current_date = actual_start
    while current_date <= today:
        date_str = current_date.isoformat()

        # Skip if already have this date
        if date_str not in history:
            total_value, item_count, card_count = calculate_historical_collection_value(
                collection, current_date, pages_cache, mode=mode
            )

            history[date_str] = {
                "total": round(total_value, 2),
                "items": item_count,
                "cards": card_count
            }
            days_backfilled += 1

        current_date += timedelta(days=1)

    # Mark how far we've backfilled
    if mode == 'acquisition':
        collection.history_backfilled_to = actual_start.isoformat()
    else:
        collection.portfolio_backfilled_to = actual_start.isoformat()

    collection.save()

    return days_backfilled
