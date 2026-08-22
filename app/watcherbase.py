
from bs4 import BeautifulSoup
import os
import shutil
import json
import time
import math
from app.page import Page
from app.listing import Listing
from app.language_libraries import *
from app.config import PAGES_DIR, ARCHIVE_DIR, IMAGES_DIR, CHANGES_DIR, DOWNLOADS_DIR, FAILED_DIR

class watcherbase():

    # --- Market price ("magic number") configuration ----------------------
    # A representative price for a card in average condition and the usual
    # language. Tunable on purpose — adjust after comparing the three methods.
    #
    # Conditions treated as "average condition". Damaged grades (LP/PL/PO) are
    # excluded; the set is broadened automatically when too few samples remain.
    GOOD_CONDITIONS = ('MT', 'NM', 'EX', 'GD')
    # Canonical-language preference order (NOT a tiebreak): the dominant
    # language is the first of these that is actually on offer. English covers
    # Western cards (they always have English listings), Japanese covers
    # Japanese-origin cards, Chinese only a card that exists solely in Chinese.
    LANGUAGE_PRIORITY = ('English', 'Japanese', 'S-Chinese', 'T-Chinese', 'Korean')
    # A higher-priority language needs at least this many listings before it
    # wins, so one mislabeled stray doesn't hijack an otherwise foreign market.
    MIN_LANGUAGE_LISTINGS = 2
    # Blend weights: transaction (realized sales) vs floor (buy-now low band).
    # These are the weights when sold data is fully trusted; the transaction's
    # share is scaled down by a confidence factor (see BLEND_SOLD_PRIOR) when
    # recent sales are sparse or stale, so blend leans on the live floor during
    # surges instead of lagging behind an old sold average.
    BLEND_W_TRANSACTION = 0.6
    BLEND_W_FLOOR = 0.4
    # Confidence in the sold price = min(1, W / BLEND_SOLD_FULL), where W is a
    # recency-weighted count of realized sales. Crucially W uses a SHORTER
    # half-life than the sold average (BLEND_SOLD_HALFLIFE below): the average is
    # smoothed over a month, but a month-old sale should not vouch for today's
    # price after a surge, so confidence must decay faster. At/above BLEND_SOLD_FULL
    # blend is the full 0.6/0.4 mix (well-sold cards unchanged); below it the sold
    # share shades to the live floor; with no fresh sales blend is the pure floor.
    BLEND_SOLD_FULL = 4.0
    BLEND_SOLD_HALFLIFE = 10.0  # days; recency horizon for trusting sold data
    # Percentile of the (outlier-filtered) asks used as the buy-now floor.
    FLOOR_PERCENTILE = 10
    # Graded-floor fallback. On a card whose raw supply has dried up, what is
    # left in the raw pool is often not a plain single at all -- a sealed box, a
    # full set, a bundle -- priced far above every slab on the same page. The
    # floor then reports a number nobody can buy this card for, while a stack of
    # PSA/BGS 9s and 10s sits there cheaper. When the raw pool is that thin AND
    # the slabs undercut it (an inversion: a slab normally costs more than raw),
    # the reference-grade slab ask is the honest buy-now number instead.
    # Requires all three: few raw asks, enough slabs to be a market, inversion.
    GRADED_FALLBACK_MAX_RAW_ASKS = 3
    GRADED_FALLBACK_MIN_GRADED_ASKS = 3
    # Only high-grade slabs stand in for a raw copy. A PSA 4 is worth less than a
    # raw NM card, so a low-grade slab would understate the floor; a 9 is the
    # conservative choice (it trades at or above raw, never below).
    GRADED_FALLBACK_MIN_GRADE = 9.0
    # Minimum samples before we trust the condition-filtered set; else broaden.
    MIN_CONDITION_SAMPLES = 3
    # Sentinel distinguishing "language not supplied" from an explicit None.
    _UNSET = object()

    # Accepted extensions for a downloaded page: our own downloader writes
    # .htm, a browser "Save page as..." writes .html.
    PAGE_EXTENSIONS = (".htm", ".html")

    # Suffixes browsers give the companion assets folder; localized per UI
    # language ("-Dateien" on a German Chrome, "_files" on an English one).
    ASSET_FOLDER_SUFFIXES = ("-Dateien", "_files")

    def is_page_file(file_name):
        return file_name.lower().endswith(watcherbase.PAGE_EXTENSIONS)

    def get_page_stem(file_name):
        """Strip the page extension, so the assets folder can be derived."""
        lowered = file_name.lower()
        for ext in watcherbase.PAGE_EXTENSIONS:
            if lowered.endswith(ext):
                return file_name[:-len(ext)]
        return file_name

    def get_asset_folders(file_name):
        """Candidate names of the saved-assets folder belonging to a page."""
        stem = watcherbase.get_page_stem(file_name)
        return [stem + suffix for suffix in watcherbase.ASSET_FOLDER_SUFFIXES]

    def get_name_from_address(address):
        return address[30:].replace('/','_')

    def get_address_from_name(name):
        return "https://www.cardmarket.com/en/" + name[:-5].replace('_','/')

    def delete_download(file_name):
        print("delete_download | deleting html " + file_name)
        os.remove(os.path.join(DOWNLOADS_DIR, file_name))
        for assets in watcherbase.get_asset_folders(file_name):
            path = os.path.join(DOWNLOADS_DIR, assets)
            if not os.path.isdir(path):
                continue
            print("delete_download | deleting folder " + assets)
            try:
                shutil.rmtree(path)
            except Exception as e:
                print("delete_download | could not delete " + assets + ": " + str(e))

    def move_to_failed(file_name):
        """Quarantine a download that raised during import into downloads/failed/.

        Unlike delete_download we keep the file (and its assets) so the offending
        page can be inspected instead of silently lost.
        """
        try:
            os.makedirs(FAILED_DIR, exist_ok=True)
            dest = os.path.join(FAILED_DIR, file_name)
            if os.path.exists(dest):
                os.remove(dest)
            shutil.move(os.path.join(DOWNLOADS_DIR, file_name), dest)
            print("move_to_failed | quarantined " + file_name)
            # Move the saved-assets folder alongside it, if present.
            for assets in watcherbase.get_asset_folders(file_name):
                assets_src = os.path.join(DOWNLOADS_DIR, assets)
                if os.path.isdir(assets_src):
                    assets_dest = os.path.join(FAILED_DIR, assets)
                    if os.path.exists(assets_dest):
                        shutil.rmtree(assets_dest)
                    shutil.move(assets_src, assets_dest)
        except Exception as e:
            print("move_to_failed | ERROR quarantining " + file_name + ": " + str(e))

    def calculate_price_average_simple(prices):
        """
        Calculate simple price average without outlier filtering.
        Use this for ended/sold listings where we want to track actual sale prices.

        Args:
            prices: List of prices (floats)

        Returns:
            float: Simple average price
        """
        if not prices:
            return 0.0
        return sum(prices) / len(prices)

    def calculate_price_average_time_weighted(price_date_pairs, half_life_days=30, reference_time=None):
        """
        Calculate time-weighted price average where recent sales have more influence.
        Uses exponential decay: weight = 2^(-days_ago / half_life)

        With half_life=30 days:
        - Today's sale: weight 1.0
        - 30 days ago: weight 0.5
        - 60 days ago: weight 0.25
        - 90 days ago: weight 0.125

        Args:
            price_date_pairs: List of (price, timestamp) tuples
            half_life_days: Days until weight is halved (default 30)
            reference_time: Reference timestamp for "now" (default: current time)
                           Use this to calculate historical weighted averages

        Returns:
            float: Time-weighted average price
        """
        import math

        if not price_date_pairs:
            return 0.0

        now = reference_time if reference_time is not None else time.time()
        total_weight = 0.0
        weighted_sum = 0.0

        for price, timestamp in price_date_pairs:
            try:
                ts = float(timestamp) if timestamp else 0
            except (ValueError, TypeError):
                ts = 0

            if ts <= 0:
                # If no valid timestamp, use minimal weight
                days_ago = 365  # Assume old
            else:
                days_ago = (now - ts) / (24 * 60 * 60)
                # Only include if the sale happened before the reference time
                if days_ago < 0:
                    continue  # Skip future sales relative to reference time

            # Exponential decay weight
            weight = math.pow(2, -days_ago / half_life_days)

            weighted_sum += price * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        return weighted_sum / total_weight

    def _recency_weight_sum(price_date_pairs, half_life_days=30, reference_time=None):
        """Sum of the exponential-decay weights used by the time-weighted average.

        Mirrors calculate_price_average_time_weighted's weighting so it reads as
        an "effective number of recent sales": many fresh sales -> large sum,
        a few old sales -> small sum. Used to gauge confidence in the sold price.
        """
        if not price_date_pairs:
            return 0.0
        now = reference_time if reference_time is not None else time.time()
        total = 0.0
        for _price, timestamp in price_date_pairs:
            try:
                ts = float(timestamp) if timestamp else 0
            except (ValueError, TypeError):
                ts = 0
            if ts <= 0:
                days_ago = 365
            else:
                days_ago = (now - ts) / (24 * 60 * 60)
                if days_ago < 0:
                    continue
            total += math.pow(2, -days_ago / half_life_days)
        return total



    def get_price_at_time(listing, target_time):
        """
        Get the price that was active for a listing at a specific point in time.
        Uses the previous_prices history to determine what price was active then.

        Args:
            listing: Listing object with price and previous_prices
            target_time: Unix timestamp of the historical point

        Returns:
            float: The price that was active at target_time
        """
        # Start with current price as candidate
        candidate_price = listing.price

        # If no previous prices, return current price
        if not listing.previous_prices:
            return candidate_price

        # Go through previous_prices in reverse order (most recent first)
        # Each entry is [old_price, change_date] - the old_price was replaced at change_date
        for prev_entry in reversed(listing.previous_prices):
            try:
                old_price = float(prev_entry[0])
                change_date_str = prev_entry[1] if len(prev_entry) > 1 else ''

                # Skip entries with no date (user requested to ignore these)
                if not change_date_str:
                    continue

                change_date = float(change_date_str)

                # If the price change happened AFTER target_time, the current candidate
                # wasn't active yet at target_time - use the old_price instead
                if change_date > target_time:
                    candidate_price = old_price
                else:
                    # This change happened before target_time, so we've found our answer
                    break
            except (ValueError, TypeError, IndexError):
                # Skip malformed entries
                continue

        return candidate_price

    def _excluded_from_raw(listing):
        """Whether a listing must stay out of the raw-card price pool.

        Archived listings are excluded by user request. Graded listings are
        excluded because a slab is a different product: a PSA 10 at 800 EUR and a
        raw NM at 40 EUR are not two samples of one price, and mixing them was
        producing phantom movers on the dashboard. Graded copies get their own
        numbers in calculate_graded_buckets.
        """
        return listing.archived or listing.is_graded()

    def calculate_historical_average(page, days_ago):
        """
        Calculate what the average price would have been X days ago.
        Includes listings that were available at that time:
        - first_date <= cutoff (existed by then)
        - AND either still active now, or ended after the cutoff date

        Args:
            page: Page object with listings
            days_ago: Number of days to look back

        Returns:
            float or None: Historical average price, or None if insufficient data
        """
        cutoff = time.time() - (days_ago * 24 * 60 * 60)

        historical_prices = []
        for listing in page.listings:
            # Skip archived and graded listings
            if watcherbase._excluded_from_raw(listing):
                continue

            try:
                first_date = float(listing.first_date) if listing.first_date else 0
            except (ValueError, TypeError):
                first_date = 0

            # Listing must have existed at the cutoff time
            if first_date <= 0 or first_date > cutoff:
                continue

            # Check if listing was still active at cutoff time
            if listing.ended:
                # For ended listings, check if they ended after the cutoff
                # The 'date' field holds the last seen date before ending
                try:
                    last_seen = float(listing.date) if listing.date else 0
                except (ValueError, TypeError):
                    last_seen = 0
                # If last seen before cutoff, it wasn't available at cutoff
                if last_seen < cutoff:
                    continue

            # Listing was available at the historical point in time
            # Use the price that was active at the cutoff time, not current price
            historical_price = watcherbase.get_price_at_time(listing, cutoff)
            historical_prices.append(historical_price)

        if historical_prices:
            return Page.calculate_price_average_robust(historical_prices)
        return None

    def calculate_historical_min(page, days_ago):
        """The lowest available price ("From") as it was X days ago.

        Uses the same availability test as calculate_historical_average, but
        returns the minimum reconstructed price instead of the average.
        """
        cutoff = time.time() - (days_ago * 24 * 60 * 60)

        historical_prices = []
        for listing in page.listings:
            if watcherbase._excluded_from_raw(listing):
                continue
            try:
                first_date = float(listing.first_date) if listing.first_date else 0
            except (ValueError, TypeError):
                first_date = 0
            if first_date <= 0 or first_date > cutoff:
                continue
            if listing.ended:
                try:
                    last_seen = float(listing.date) if listing.date else 0
                except (ValueError, TypeError):
                    last_seen = 0
                if last_seen < cutoff:
                    continue
            historical_prices.append(watcherbase.get_price_at_time(listing, cutoff))

        return min(historical_prices) if historical_prices else None

    def calculate_historical_ended_average(page, days_ago):
        """
        Calculate what the time-weighted average price of ended listings would have been X days ago.
        Only includes listings that were already ended by that time, weighted by recency
        relative to that historical point.

        Args:
            page: Page object with listings
            days_ago: Number of days to look back

        Returns:
            float or None: Historical ended average price, or None if insufficient data
        """
        cutoff = time.time() - (days_ago * 24 * 60 * 60)

        historical_ended_pairs = []
        for listing in page.listings:
            # Skip archived and graded listings
            if watcherbase._excluded_from_raw(listing):
                continue

            # Only consider currently ended listings
            if not listing.ended:
                continue

            # Check if the listing was already ended at the cutoff time
            # The 'date' field holds the last seen date (when it ended)
            try:
                last_seen = float(listing.date) if listing.date else 0
            except (ValueError, TypeError):
                last_seen = 0

            # If the listing ended before or at the cutoff, it was ended at that time
            if last_seen > 0 and last_seen <= cutoff:
                historical_ended_pairs.append((listing.price, listing.date))

        if historical_ended_pairs:
            # Calculate time-weighted average relative to the cutoff point
            return watcherbase.calculate_price_average_time_weighted(
                historical_ended_pairs, reference_time=cutoff
            )
        return None

    def calculate_historical_available_count(page, days_ago):
        """
        Calculate how many items were available X days ago.
        A listing was available if:
        - first_date <= cutoff (existed by then)
        - AND either not ended now, or ended after the cutoff date

        Counts item quantities (summing listing.quantity), not listings, so it is
        consistent with current_available in calculate_all_period_averages. The
        listing's current quantity is used as the best available proxy for its
        quantity at the historical point.

        Args:
            page: Page object with listings
            days_ago: Number of days to look back

        Returns:
            int: Number of items available at that time
        """
        cutoff = time.time() - (days_ago * 24 * 60 * 60)
        count = 0

        for listing in page.listings:
            # Skip archived and graded listings
            if watcherbase._excluded_from_raw(listing):
                continue

            try:
                first_date = float(listing.first_date) if listing.first_date else 0
            except (ValueError, TypeError):
                first_date = 0

            # Listing must have existed at the cutoff time
            if first_date <= 0 or first_date > cutoff:
                continue

            # Check if listing was still active at cutoff time
            if listing.ended:
                # For ended listings, check if they ended after the cutoff
                try:
                    last_seen = float(listing.date) if listing.date else 0
                except (ValueError, TypeError):
                    last_seen = 0
                # If last seen before cutoff, it wasn't available at cutoff
                if last_seen < cutoff:
                    continue

            # Listing was available at the historical point in time
            count += listing.quantity

        return count

    def calculate_availability_changes(page, days_ago):
        """
        Calculate how many items were added and removed within the last X days.

        Added: Listings that are currently available and were first seen within the period
        Removed: Listings that ended within the period (regardless of when first seen)

        Counts item quantities (summing listing.quantity), not listings, so the net
        supply change stays consistent with current_available / historical_available.

        Args:
            page: Page object with listings
            days_ago: Number of days to look back

        Returns:
            tuple: (added_items, removed_items)
        """
        cutoff = time.time() - (days_ago * 24 * 60 * 60)
        added = 0
        removed = 0

        for listing in page.listings:
            # Skip archived and graded listings
            if watcherbase._excluded_from_raw(listing):
                continue

            try:
                first_date = float(listing.first_date) if listing.first_date else 0
            except (ValueError, TypeError):
                first_date = 0

            try:
                last_seen = float(listing.date) if listing.date else 0
            except (ValueError, TypeError):
                last_seen = 0

            # Check if listing is currently active (not ended)
            if not listing.ended:
                # Active listing - check if it's new since cutoff
                if first_date > cutoff:
                    added += listing.quantity
            else:
                # Ended listing - check if it ended within the period
                # (last_seen > cutoff means it ended after the cutoff, i.e., within the period)
                if last_seen > cutoff:
                    removed += listing.quantity

        return (added, removed)

    # ------------------------------------------------------------------
    # Market price ("magic number")
    # ------------------------------------------------------------------
    def _iqr_bounds(values):
        """Return (lower, upper) outlier bounds for a list of numbers, or None.

        Mirrors the IQR filter in Page.calculate_price_average_robust.
        """
        vals = sorted(values)
        n = len(vals)
        if n < 4:
            return None
        q1 = vals[n // 4]
        q3 = vals[3 * n // 4]
        iqr = q3 - q1
        if iqr <= 0:
            return None
        return (q1 - 1.5 * iqr, q3 + 1.5 * iqr)

    def _percentile(sorted_vals, pct):
        """Linear-interpolated percentile of an already-sorted list."""
        if not sorted_vals:
            return 0.0
        if len(sorted_vals) == 1:
            return sorted_vals[0]
        k = (len(sorted_vals) - 1) * (pct / 100.0)
        lo = math.floor(k)
        hi = math.ceil(k)
        if lo == hi:
            return sorted_vals[int(k)]
        return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)

    def _grade_selected(listing, grade):
        """Whether a listing belongs to the requested slice of the market.

        ``grade`` is 'raw' (ungraded only), 'graded' (any slab), a
        (company, number) pair for one specific slab grade, or None for
        everything.
        """
        if grade is None:
            return True
        if grade == 'raw':
            return not listing.is_graded()
        if grade == 'graded':
            return listing.is_graded()
        company, number = grade
        return listing.grade_company == company and listing.grade == number

    def _listings_at_time(page, at_time, grade='raw'):
        """Split non-archived listings into (active, sold) at a point in time.

        Each entry is (listing, price). When at_time is None the current snapshot
        is used (price = listing.price). Otherwise prices/availability are
        reconstructed for that historical moment, reusing the same availability
        tests as calculate_historical_average / calculate_historical_ended_average.

        ``grade`` selects which slice of the market to report on; it defaults to
        raw so every existing caller gets ungraded prices. See _grade_selected.
        """
        active, sold = [], []
        for l in page.listings:
            if l.archived:
                continue
            if not watcherbase._grade_selected(l, grade):
                continue

            if at_time is None:
                if l.ended:
                    sold.append((l, l.price))
                else:
                    active.append((l, l.price))
                continue

            try:
                first_date = float(l.first_date) if l.first_date else 0
            except (ValueError, TypeError):
                first_date = 0
            try:
                last_seen = float(l.date) if l.date else 0
            except (ValueError, TypeError):
                last_seen = 0

            # Sold by the cutoff: ended and last seen at/before the cutoff.
            if l.ended and last_seen > 0 and last_seen <= at_time:
                sold.append((l, l.price))
                continue

            # Active at the cutoff: existed by then and not yet ended back then.
            if first_date <= 0 or first_date > at_time:
                continue
            if l.ended and last_seen < at_time:
                continue
            active.append((l, watcherbase.get_price_at_time(l, at_time)))

        return active, sold

    def dominant_language(active_listings):
        """The canonical trading language, chosen by availability not by supply.

        Walks LANGUAGE_PRIORITY and returns the first language that is on offer
        (English → Japanese → Chinese → …). Choosing by availability — rather
        than "whichever language has the most listings right now" — keeps the
        language stable over time: supply shifts day to day, and a supply-based
        choice flips the language back and forth, which makes the floor/blend
        jump between the price levels of different languages. A higher-priority
        language must clear MIN_LANGUAGE_LISTINGS so a single stray (e.g. a
        mislabeled English listing on a Japanese-only product) can't hijack it.
        Languages outside the priority list (German/French/… only) fall back to
        the most-supplied one.
        """
        totals = {}
        for l in active_listings:
            totals[l.language] = totals.get(l.language, 0) + max(1, l.quantity or 1)
        if not totals:
            return None
        solid = {lang: n for lang, n in totals.items()
                 if n >= watcherbase.MIN_LANGUAGE_LISTINGS}
        pool = solid or totals
        for lang in watcherbase.LANGUAGE_PRIORITY:
            if lang in pool:
                return lang
        return max(pool, key=lambda k: pool[k])

    def _graded_floor_fallback(page, at_time, lang, n_raw_asks, raw_floor):
        """Slab ask standing in for a raw floor the raw pool cannot supply.

        Returns 0.0 unless every condition in GRADED_FALLBACK_* holds: the raw
        pool is down to a handful of asks, there are enough slabs at or above
        GRADED_FALLBACK_MIN_GRADE to call a market, and those slabs undercut the
        raw floor. That last one is the actual tell -- a slab costs more than the
        same card raw, so a cheaper slab means the "raw" asks are not plain
        singles (sealed product, full sets, bundles) or are simply not sellable
        at that price. See the constants for the full rationale.

        A raw floor of 0 (nothing raw on offer at all) counts as the extreme case
        and is filled in rather than left as a silent zero.

        Language-matched like calculate_graded_premium: an English raw floor and
        a Japanese slab are two different markets, and substituting across them
        swaps one wrong number for another. Deliberately no condition filter --
        the grade IS the condition of a slab -- and a plain minimum rather than a
        percentile band, since these pools are one to three listings.
        """
        if n_raw_asks > watcherbase.GRADED_FALLBACK_MAX_RAW_ASKS:
            return 0.0

        active, _ = watcherbase._listings_at_time(page, at_time, 'graded')
        prices = [p for (l, p) in active
                  if (lang is None or l.language == lang)
                  and l.grade is not None
                  and l.grade >= watcherbase.GRADED_FALLBACK_MIN_GRADE]
        if len(prices) < watcherbase.GRADED_FALLBACK_MIN_GRADED_ASKS:
            return 0.0

        graded_floor = min(prices)
        if raw_floor > 0 and graded_floor >= raw_floor:
            return 0.0
        return graded_floor

    def calculate_market_prices(page, at_time=None, lang=_UNSET, grade='raw'):
        """Representative market price computed three ways.

        Returns {'blend', 'transaction', 'floor', 'language', 'n_sold', 'n_ask',
        'basis'}.
        - transaction: time-weighted, IQR-filtered average of realized sales.
        - floor: low band (FLOOR_PERCENTILE) of outlier-filtered current asks.
        - blend: weighted mix of transaction and floor (the all-round number).
        All filtered to the dominant language and "average condition" grades.

        ``lang`` pins the language to filter on. When left unset it is the
        dominant language of the snapshot at ``at_time``. Callers building a
        time series should pin a single canonical language across every day,
        otherwise the dominant language flips as the reconstructed supply
        changes and the floor/blend/sold numbers jump between price levels of
        different languages.

        ``grade`` defaults to raw. For a card only ever offered as slabs that
        would leave nothing to price, so the whole pool is used instead and
        'basis' reports 'all' rather than 'raw' -- better an honestly labelled
        graded price than a silent zero.

        One further edge case: a raw pool too thin and too dear to be a market
        (see _graded_floor_fallback) hands the floor over to the cheapest
        high-grade slab and reports 'basis' as 'graded-floor'. Only the floor is
        substituted -- 'transaction' stays the realized raw sales -- but blend is
        mixed from the substituted floor, since that is the buy-now side.
        """
        basis = 'raw' if grade == 'raw' else 'selected'
        active, sold = watcherbase._listings_at_time(page, at_time, grade)
        if grade == 'raw' and not active and not sold:
            active, sold = watcherbase._listings_at_time(page, at_time, None)
            basis = 'all'
        if lang is watcherbase._UNSET:
            lang = (watcherbase.dominant_language([l for l, _ in active])
                    or watcherbase.dominant_language([l for l, _ in sold]))

        def filtered(pairs):
            same_lang = [(l, p) for (l, p) in pairs if lang is None or l.language == lang]
            good = [(l, p) for (l, p) in same_lang if l.condition in watcherbase.GOOD_CONDITIONS]
            if len(good) >= watcherbase.MIN_CONDITION_SAMPLES:
                return good
            # Too few in good condition — broaden conditions, but never broaden
            # across languages: mixing in cheaper languages is what makes the
            # floor jump around. No same-language samples means no price here.
            return same_lang

        active_f = filtered(active)
        sold_f = filtered(sold)

        # Transaction: realized sales, IQR-filtered then time-weighted.
        transaction = 0.0
        sold_weight = 0.0  # recency-weighted count of sales -> confidence in sold
        if sold_f:
            sold_pairs = [(p, l.date) for (l, p) in sold_f]
            bounds = watcherbase._iqr_bounds([p for p, _ in sold_pairs])
            if bounds:
                kept = [(p, d) for (p, d) in sold_pairs if bounds[0] <= p <= bounds[1]]
                if kept:
                    sold_pairs = kept
            transaction = watcherbase.calculate_price_average_time_weighted(
                sold_pairs, reference_time=at_time)
            sold_weight = watcherbase._recency_weight_sum(
                sold_pairs, half_life_days=watcherbase.BLEND_SOLD_HALFLIFE,
                reference_time=at_time)

        # Floor: low band of outlier-filtered current asks.
        floor = 0.0
        if active_f:
            asks = sorted(p for _, p in active_f)
            bounds = watcherbase._iqr_bounds(asks)
            if bounds:
                kept = [p for p in asks if bounds[0] <= p <= bounds[1]]
                if kept:
                    asks = kept
            floor = watcherbase._percentile(asks, watcherbase.FLOOR_PERCENTILE)

        # Edge case: a raw floor priced off a couple of leftover oddities, with
        # cheaper slabs sitting right next to them on the page.
        if basis == 'raw':
            substitute = watcherbase._graded_floor_fallback(
                page, at_time, lang, len(active_f), floor)
            if substitute > 0:
                floor = substitute
                basis = 'graded-floor'

        # Blend: adaptive mix of the sold price and the live floor. The sold
        # side's weight is scaled by confidence = min(1, W / BLEND_SOLD_FULL),
        # where W is the recency-weighted count of realized sales. With enough
        # fresh sales (W >= BLEND_SOLD_FULL) confidence is 1 and blend is the full
        # 0.6/0.4 mix; when sales are sparse or stale confidence falls toward 0
        # and the sold share is handed to the floor, so blend tracks the live
        # market during a surge instead of lagging behind an old sold average.
        if transaction > 0 and floor > 0:
            wt = watcherbase.BLEND_W_TRANSACTION
            wf = watcherbase.BLEND_W_FLOOR
            conf = min(1.0, sold_weight / watcherbase.BLEND_SOLD_FULL)
            tw = wt * conf                 # effective sold weight
            fw = wf + wt * (1 - conf)      # floor absorbs the rest
            blend = (tw * transaction + fw * floor) / (wt + wf)
        elif transaction > 0:
            blend = transaction
        else:
            blend = floor

        return {
            'blend': round(blend, 2),
            'transaction': round(transaction, 2),
            'floor': round(floor, 2),
            'language': lang,
            'n_sold': len(sold_f),
            'n_ask': len(active_f),
            'basis': basis,
        }

    def calculate_graded_buckets(page, at_time=None, max_sold=8, lang=None):
        """Per-(company, grade) value for the graded copies of this card.

        Keyed by the display label ("PSA 10", "BGS 9.5"), because a PSA 10 and a
        CGC 10 are not interchangeable products and do not trade at the same
        price. Each bucket carries:

            company, grade      the pair, split out for filtering
            floor               lowest current ask
            available           item count on offer (quantities, like page.available)
            n_ask               how many listings that is
            sold                recent realized prices, newest first: [[price, ts]]
            n_sold              how many ended listings exist in total
            last_sold           the most recent one, or 0

        Deliberately no IQR filter, no percentile band and no time-weighted
        average: a graded bucket is typically one to three listings, where those
        tools describe noise rather than a market. What is meaningful for a slab
        is the ask floor and the prices copies actually went away at, so that is
        what is reported and the caller can judge the sample size from n_ask /
        n_sold. Note "sold" means the listing ended -- it may also have been
        delisted or edited, the same caveat the raw ended-average carries.

        ``lang`` restricts the buckets to one language. Left off by default: a
        slab pool split by language is usually one listing or none, and the point
        of the card page is to show every slab on offer. Pass it when comparing
        against a language-filtered raw price (see calculate_graded_premium).
        """
        active, sold = watcherbase._listings_at_time(page, at_time, 'graded')
        if lang is not None:
            active = [(l, p) for (l, p) in active if l.language == lang]
            sold = [(l, p) for (l, p) in sold if l.language == lang]

        buckets = {}

        def bucket_for(listing):
            label = listing.grade_label()
            if label not in buckets:
                buckets[label] = {
                    'company': listing.grade_company,
                    'grade': listing.grade,
                    'floor': 0.0,
                    'available': 0,
                    'n_ask': 0,
                    'sold': [],
                    'n_sold': 0,
                    'last_sold': 0.0,
                }
            return buckets[label]

        for listing, price in active:
            bucket = bucket_for(listing)
            bucket['n_ask'] += 1
            bucket['available'] += listing.quantity
            if bucket['floor'] == 0.0 or price < bucket['floor']:
                bucket['floor'] = price

        for listing, price in sold:
            bucket = bucket_for(listing)
            bucket['n_sold'] += 1
            try:
                ts = float(listing.date) if listing.date else 0.0
            except (ValueError, TypeError):
                ts = 0.0
            bucket['sold'].append([round(price, 2), ts])

        for bucket in buckets.values():
            bucket['floor'] = round(bucket['floor'], 2)
            bucket['sold'].sort(key=lambda entry: entry[1], reverse=True)
            bucket['last_sold'] = bucket['sold'][0][0] if bucket['sold'] else 0.0
            del bucket['sold'][max_sold:]

        return buckets

    def calculate_graded_premium(page, market=None):
        """What the cheapest top-grade slab costs as a multiple of a raw copy.

        e.g. 12.5 when the top grade can be had for 500 EUR against a 40 EUR raw
        floor. 0 when either side is missing.

        "Top grade" is the highest grade with a live ask, and where several
        companies offer it the cheapest one wins -- taking any other bucket at
        that grade would report the premium of whichever grader happens to sort
        first, which produced premiums below 1.0x on real pages.

        Both sides are restricted to the raw market's language. Without that the
        comparison silently crosses markets: one real page prices its raw floor
        off two English full-set listings at 2100 EUR while every slab on it is
        Portuguese or German, which read as a 0.12x "premium". Returns 0 when the
        card has no slab in that language, which is honest -- there is nothing to
        compare. Likewise 0 when the market floor itself came from a slab
        ('basis' 'graded-floor'): that would be a slab over a slab, not a premium.
        """
        market = watcherbase.calculate_market_prices(page) if market is None else market

        raw_floor = market.get('floor', 0) or 0
        if raw_floor <= 0 or market.get('basis') != 'raw':
            return 0.0

        buckets = watcherbase.calculate_graded_buckets(page, lang=market.get('language'))
        priced = [b for b in buckets.values() if b['floor'] > 0 and b['grade'] is not None]
        if not priced:
            return 0.0
        top_grade = max(b['grade'] for b in priced)
        top_floor = min(b['floor'] for b in priced if b['grade'] == top_grade)
        return round(top_floor / raw_floor, 2)

    def calculate_market_price_series(page, max_days=365):
        """Daily history of the three market prices for the page's price graph.

        Returns {'labels', 'blend', 'transaction', 'floor'} where labels are
        dd.mm.yyyy strings (matching the graph) and each series uses None for days
        with no usable data.
        """
        from datetime import datetime, timedelta

        firsts = []
        for l in page.listings:
            try:
                fd = float(l.first_date) if l.first_date else 0
            except (ValueError, TypeError):
                fd = 0
            if fd > 0:
                firsts.append(fd)
        if not firsts:
            return {'labels': [], 'blend': [], 'transaction': [], 'floor': []}

        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = datetime.fromtimestamp(min(firsts)).replace(hour=0, minute=0, second=0, microsecond=0)
        earliest = today - timedelta(days=max_days)
        if start < earliest:
            start = earliest

        # Pin one canonical language for the whole series so the floor/blend/
        # sold lines track a single market instead of flipping between
        # languages as the reconstructed supply changes day to day. The current
        # snapshot's dominant language is the anchor (matching the search view).
        canonical_lang = watcherbase.calculate_market_prices(page)['language']

        labels, blend, transaction, floor = [], [], [], []
        day = start
        while day <= today:
            mp = watcherbase.calculate_market_prices(
                page, at_time=day.timestamp(), lang=canonical_lang)
            labels.append(day.strftime('%d.%m.%Y'))
            blend.append(mp['blend'] if mp['blend'] > 0 else None)
            transaction.append(mp['transaction'] if mp['transaction'] > 0 else None)
            floor.append(mp['floor'] if mp['floor'] > 0 else None)
            day += timedelta(days=1)

        return {'labels': labels, 'blend': blend, 'transaction': transaction, 'floor': floor}

    def calculate_all_period_averages(page):
        """
        Calculate historical averages for all time periods, for both
        available (active) and ended (sold) listings.

        Returns dict with format:
        {
            'current_avg': float,
            'current_ended_avg': float,
            'current_available': int,
            '1w': {
                'historical_avg': float or None,
                'change': float or None,
                'historical_ended_avg': float or None,
                'ended_change': float or None,
                'historical_available': int or None,
                'available_change': int or None
            },
            ...
        }
        """
        periods = {
            '1w': 7,
            '1m': 30,
            '2m': 60,
            '6m': 180
        }

        # Everything below describes the RAW card. Graded copies are excluded by
        # _excluded_from_raw and reported separately in 'graded' at the bottom.
        current_prices = [l.price for l in page.listings
                          if not l.ended and not watcherbase._excluded_from_raw(l)]
        current_avg = Page.calculate_price_average_robust(current_prices) if current_prices else 0
        current_min = min(current_prices) if current_prices else 0

        # Sum quantities of current available listings
        current_available = sum(l.quantity for l in page.listings
                                if not l.ended and not watcherbase._excluded_from_raw(l))

        # Calculate current ended average using time-weighted approach
        # Recent sales have more influence than older sales
        ended_price_date_pairs = []
        for l in page.listings:
            if l.ended and not watcherbase._excluded_from_raw(l):
                ended_price_date_pairs.append((l.price, l.date))
        current_ended_avg = watcherbase.calculate_price_average_time_weighted(ended_price_date_pairs) if ended_price_date_pairs else 0

        market = watcherbase.calculate_market_prices(page)
        graded = watcherbase.calculate_graded_buckets(page)

        result = {
            'current_avg': round(current_avg, 2),
            'current_ended_avg': round(current_ended_avg, 2),
            'current_available': current_available,
            'current_min': round(current_min, 2),
            'market': market,
            'current_available_graded': sum(b['available'] for b in graded.values()),
            'graded': graded,
            'graded_premium': watcherbase.calculate_graded_premium(page, market=market),
        }

        for period_name, days in periods.items():
            period_data = {}

            # Market price as it was at the start of the period (for movers).
            period_cutoff = time.time() - (days * 24 * 60 * 60)
            period_data['market'] = watcherbase.calculate_market_prices(page, at_time=period_cutoff)

            # Available listings - price average
            historical_avg = watcherbase.calculate_historical_average(page, days)
            if historical_avg is not None and current_avg > 0:
                period_data['historical_avg'] = round(historical_avg, 2)
                period_data['change'] = round(current_avg - historical_avg, 2)
            else:
                period_data['historical_avg'] = None
                period_data['change'] = None

            # Lowest available ("From") price as it was at the start of the period
            historical_min = watcherbase.calculate_historical_min(page, days)
            period_data['historical_min'] = round(historical_min, 2) if historical_min is not None else None

            # Available listings - count and detailed changes
            historical_available = watcherbase.calculate_historical_available_count(page, days)
            added, removed = watcherbase.calculate_availability_changes(page, days)
            period_data['historical_available'] = historical_available if historical_available > 0 else None
            period_data['available_change'] = current_available - historical_available if historical_available > 0 else None
            period_data['listings_added'] = added
            period_data['listings_removed'] = removed

            # Ended listings
            historical_ended_avg = watcherbase.calculate_historical_ended_average(page, days)
            if historical_ended_avg is not None and current_ended_avg > 0:
                period_data['historical_ended_avg'] = round(historical_ended_avg, 2)
                period_data['ended_change'] = round(current_ended_avg - historical_ended_avg, 2)
            else:
                period_data['historical_ended_avg'] = None
                period_data['ended_change'] = None

            result[period_name] = period_data

        return result

    def update_price_history_for_page(page):
        """Recompute and persist one page's metrics in price_history.json.

        Used after an in-app edit (archive / unarchive / delete a listing) so the
        search view's stored prices, floor, and available count reflect the change
        without waiting for the next download/import.
        """
        ph_path = os.path.join(CHANGES_DIR, "price_history.json")
        history = {}
        if os.path.exists(ph_path):
            try:
                with open(ph_path, "r", encoding="utf-8") as f:
                    history = json.load(f)
            except (json.JSONDecodeError, IOError):
                history = {}

        metrics = watcherbase.calculate_all_period_averages(page)

        # Preserve the last-download diff (inserted/sold and the change deltas),
        # but refresh its headline prices so the default "last" view also matches
        # the now-current values after the edit.
        prev = history.get(page.canonical_name, {})
        last_download = prev.get('last_download')
        if last_download:
            last_download = dict(last_download)
            last_download['avg'] = metrics['current_avg']
            last_download['ended_avg'] = metrics['current_ended_avg']
            last_download['min'] = metrics['current_min']
            last_download['floor'] = (metrics.get('market') or {}).get('floor', 0) or 0
            metrics['last_download'] = last_download

        history[page.canonical_name] = metrics
        with open(ph_path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)

    def get_page(page_name):
        active_page = Page()
        active_page.canonical_name = page_name[:-5]
        active_page.import_page(os.path.join(PAGES_DIR,page_name))
        return active_page

    def toggle_archive(page_name):
        """
        Toggle a page between archived and active state.
        Moves the file between pages/ and archive/ folders.
        Handles .json format files.

        Args:
            page_name: Name of the .json file

        Returns:
            bool: True if archived, False if unarchived
        """
        pages_path = os.path.join(PAGES_DIR, page_name)
        archive_path = os.path.join(ARCHIVE_DIR, page_name)

        # Check if file is currently in pages/ (active)
        if os.path.exists(pages_path):
            # Move to archive
            shutil.move(pages_path, archive_path)
            print(f"toggle_archive | Archived: {page_name}")
            return True
        # Check if file is in archive/
        elif os.path.exists(archive_path):
            # Move to active pages
            shutil.move(archive_path, pages_path)
            print(f"toggle_archive | Unarchived: {page_name}")
            return False
        else:
            print(f"toggle_archive | ERROR: File not found in pages/ or archive/: {page_name}")
            return None
    
    def save_image(path,new_path):
        try:
            if os.path.exists(new_path):
                return
            shutil.copy2(path, new_path)
            print("watcherbase.save_image | new image saved under " + new_path)
        except Exception as e:
            print("save_image: ERROR: " + str(e))

    def _parse_listings(table_body, page, timestamp, report):
        """Parse the article rows of one page, isolating per-row failures.

        A single malformed row is skipped and counted in report['rows_skipped']
        rather than aborting the whole page. Returns the list of parsed prices.
        """
        prices = []
        if table_body is None:
            return prices
        for row in table_body.find_all('div', attrs={'class':'article-row'}):
            try:
                # skip rows that sell playsets, those are usually not actual playsets, but some other random combinations
                if row.find('span',attrs={'data-bs-original-title':'Playset'}):
                    continue
                # skip additional rows by active seller
                if "stockRow" in row["id"] or "shoppingCartRow" in row["id"]:
                    continue
                listing = Listing()
                listing.card = page.card
                listing.parse_from_row(row)
                listing.date = timestamp
                listing.canonical_name = page.canonical_name
                page.listings.append(listing)
                prices.append(listing.price)
            except Exception as e:
                report['rows_skipped'] += 1
                print("import_all_pages | skipping malformed row: " + str(e))
        return prices

    def _import_one_file(file_name, timestamp, price_history, report):
        """Parse and merge a single downloaded .htm file.

        Returns 'imported' on success or 'skipped' for a structurally-invalid
        page (which is deleted). Unexpected errors propagate to the caller, which
        quarantines the file in downloads/failed/.
        """
        with open(os.path.join(DOWNLOADS_DIR, file_name),'r',encoding="utf-8") as f:
            content = f.read()

        parsed_html = BeautifulSoup(content,features="lxml")
        if not parsed_html.body:
            watcherbase.delete_download(file_name)
            print("import_all_pages | no html found")
            return 'skipped'
        table_body = parsed_html.body.find('div', attrs={'class':'table-body'})

        page = Page()
        if not parsed_html.find_all('link'):
            watcherbase.delete_download(file_name)
            print("import_all_pages | no link found")
            return 'skipped'
        page.canonical_name = watcherbase.get_name_from_address(parsed_html.find_all('link')[0]['href'])

        if not parsed_html.body.find('div',attrs={'class':'page-title-container'}):
            watcherbase.delete_download(file_name)
            print("import_all_pages | no page-title found")
            return 'skipped'
        page.card = parsed_html.body.find('div',attrs={'class':'page-title-container'}).find('h1').find(string=True,recursive=False).replace('Ã©','e')

        if not parsed_html.body.find('div',attrs={'id':'articleFilterSellerLocation'}):
            watcherbase.delete_download(file_name)
            print("import_all_pages | no seller location filter found")
            return 'skipped'
        checkmark = parsed_html.body.find('div',attrs={'id':'articleFilterSellerLocation'}).find('input',attrs={'class':'form-check-input'})
        checked_country = parsed_html.body.find('div',attrs={'id':'articleFilterSellerLocation'}).find('div',attrs={'class','form-check'}).find('label').text
        page.only_germany = False
        if 'checked' in checkmark.attrs and ("Deutschland" in checked_country or "Germany" in checked_country):
            page.only_germany = (checkmark['checked'] == 'checked')
        print("import_all_pages | only listings from germany: " + str(page.only_germany))

        # get the active languages
        all_languages = []
        product_languages = parsed_html.body.find('div',attrs={'id':'articleFilterProductLanguage'})
        if not product_languages:
            watcherbase.delete_download(file_name)
            print("import_all_pages | no language filter found")
            return 'skipped'
        for language in product_languages.find_all('div',attrs={'class':'form-check'}):
            all_languages.append(language_to_english[language.text] if language.text in language_to_english else language.text)
            checkbox = language.find('input',attrs={'class':'form-check-input'})
            if "checked" in checkbox.attrs and checkbox['checked'] == "checked":
                page.languages.append(language.text)
        # if no language is checked, all languages in the list are active
        if len(page.languages) == 0:
            for language in all_languages:
                page.languages.append(language_to_english[language] if language in language_to_english else language)

        # get the product image
        card_slideshow = parsed_html.body.find('div',attrs={'class':'card-slideshow'})
        if card_slideshow:
            image_path = card_slideshow.find_all('div',attrs={'class':'slide'})[1].find('img')['src'].replace('%20',' ').replace('%C3%A9','é')
        else:
            image_path = parsed_html.body.find('section',attrs={'id':'image'}).find('img')['src'].replace('%20',' ').replace('%C3%A9','é')
        page.image = "data/images/" + (page.canonical_name) + ".jpg"
        image_dest = os.path.join(IMAGES_DIR, page.canonical_name + ".jpg")
        if image_path.startswith("http://") or image_path.startswith("https://") or image_path.startswith("//"):
            # Selenium download: image was downloaded by the downloader, not a local file
            if not os.path.exists(image_dest):
                print(f"import_all_pages | WARNING: image not found at {image_dest}")
        else:
            watcherbase.save_image(os.path.join(DOWNLOADS_DIR, image_path), image_dest)

        # get the set the product is from
        page.set = parsed_html.body.find('div',attrs={'class':'page-title-container'}).find('h1').find('span').text.replace('Ã©','e')

        # check if the user loaded all listings
        if parsed_html.find('button',attrs={'id':'loadMoreButton'}):
            page.loadMoreButton = True

        for item in parsed_html.find_all('button', attrs={'class':'mt-2 text-muted text-center'}):
            if item.text == "We only show the first 300 articles. Please use the filters for more precise results.":
                page.loadMoreButton = True
                break

        # iterate over the available listings and parse them (per-row isolated)
        prices = watcherbase._parse_listings(table_body, page, timestamp, report)
        page.price_average = Page.calculate_price_average_robust(prices)

        # open the corresponding old page and compare it with the newly created one
        old_page = Page()
        old_page.canonical_name = page.canonical_name
        old_page.import_page(old_page.canonical_name+".json")

        # Calculate averages BEFORE update (excluding archived and ended)
        old_available_prices = [l.price for l in old_page.listings if not l.ended and not l.archived]
        old_available_avg = Page.calculate_price_average_robust(old_available_prices) if old_available_prices else 0
        old_ended_prices = [(l.price, l.date) for l in old_page.listings if l.ended and not l.archived]
        old_ended_avg = watcherbase.calculate_price_average_time_weighted(old_ended_prices) if old_ended_prices else 0
        old_min = min(old_available_prices) if old_available_prices else 0
        old_floor = watcherbase.calculate_market_prices(old_page)['floor']

        old_page.update_page(page)
        old_page.save()
        print("import_all_pages | page saved under " + os.path.join(PAGES_DIR,(old_page.canonical_name+".json")))
        watcherbase.delete_download(file_name)

        # Calculate averages AFTER update (excluding archived and ended)
        new_available_prices = [l.price for l in old_page.listings if not l.ended and not l.archived]
        new_available_avg = Page.calculate_price_average_robust(new_available_prices) if new_available_prices else 0
        available_avg_change = new_available_avg - old_available_avg
        new_ended_prices = [(l.price, l.date) for l in old_page.listings if l.ended and not l.archived]
        new_ended_avg = watcherbase.calculate_price_average_time_weighted(new_ended_prices) if new_ended_prices else 0
        ended_avg_change = new_ended_avg - old_ended_avg

        # Calculate period-based price averages
        metrics = watcherbase.calculate_all_period_averages(old_page)
        price_history[page.canonical_name] = metrics

        new_min = metrics.get('current_min', 0) or 0
        new_floor = (metrics.get('market') or {}).get('floor', 0) or 0

        # Add last_download section with all metrics
        price_history[page.canonical_name]['last_download'] = {
            'avg': round(new_available_avg, 2),
            'avg_change': round(available_avg_change, 2),
            'ended_avg': round(new_ended_avg, 2),
            'ended_avg_change': round(ended_avg_change, 2),
            'min': round(new_min, 2),
            'min_change': round(new_min - old_min, 2),
            'floor': round(new_floor, 2),
            'floor_change': round(new_floor - old_floor, 2),
            'inserted': old_page.inserted,
            'sold': old_page.sold
        }
        return 'imported'

    def import_all_pages():
        """Import all downloaded .htm files. One bad page or row can't abort the run.

        Returns a report: {imported, skipped, failed:[file names], rows_skipped}.
        """
        report = {'imported': 0, 'skipped': 0, 'failed': [], 'rows_skipped': 0}
        price_history = {}
        if not os.path.isdir(DOWNLOADS_DIR):
            print("watcherbase | no downloads folder found")
            return report
        file_list = os.listdir(DOWNLOADS_DIR)
        file_info_list = [(file_name, os.path.getmtime(os.path.join(DOWNLOADS_DIR, file_name))) for file_name in file_list if watcherbase.is_page_file(file_name) and os.path.isfile(os.path.join(DOWNLOADS_DIR, file_name))]
        sorted_file_info_list = sorted(file_info_list, key=lambda x: x[1])
        for file_name,timestamp in sorted_file_info_list:
            print("import_all_pages | importing " + file_name)
            try:
                status = watcherbase._import_one_file(file_name, timestamp, price_history, report)
                if status == 'imported':
                    report['imported'] += 1
                else:
                    report['skipped'] += 1
            except Exception as e:
                # One bad page must never take down the import or the app.
                report['failed'].append(file_name)
                print("import_all_pages | ERROR importing " + file_name + ": " + str(e))
                watcherbase.move_to_failed(file_name)

        # Load existing price_history.json, merge with new data, and save
        existing_price_history = {}
        if os.path.exists(os.path.join(CHANGES_DIR, "price_history.json")):
            try:
                with open(os.path.join(CHANGES_DIR, "price_history.json"), "r", encoding="utf-8") as f:
                    existing_price_history = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_price_history = {}

        # Merge new price history data
        for key, value in price_history.items():
            existing_price_history[key] = value

        # Save updated price history
        with open(os.path.join(CHANGES_DIR, "price_history.json"), "w", encoding="utf-8") as f:
            json.dump(existing_price_history, f, indent=2)

        print(f"import_all_pages | done: {report['imported']} imported, "
              f"{report['skipped']} skipped, {len(report['failed'])} failed, "
              f"{report['rows_skipped']} rows skipped")
        return report
