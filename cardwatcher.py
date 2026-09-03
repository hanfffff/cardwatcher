import argparse
import logging
import os
import json
import webbrowser
import threading

# Parse args and initialize data directory BEFORE importing app modules
# This ensures all modules get the correct paths
def _early_init():
    parser = argparse.ArgumentParser(description='CardWatcher Flask Application')
    parser.add_argument('-p', '--port', type=int, default=5000, help='Port to run the server on (default: 5000)')
    parser.add_argument('--no-browser', action='store_true', help='Do not open browser automatically')
    args = parser.parse_args()

    # Initialize data directory (prompts user on first run)
    from app.config import initialize_data_dir, update_paths
    data_dir = initialize_data_dir()
    update_paths(data_dir)

    return args

# Only run early init when this is the main script
_startup_args = None
if __name__ == '__main__':
    _startup_args = _early_init()

# Now import app modules - they will get the correct paths
from flask import Flask, render_template, request, redirect, jsonify, send_from_directory
from flask_session import Session
from app.language_libraries import *
from app.grading_libraries import COMPANY_LABELS, COMPANY_ORDER

# The companies offered in the manual grade editor, in the filter's display
# order, as [value, label] pairs. Taken straight from COMPANY_ORDER so a company
# added to grading_libraries shows up here without a second edit. The unknown
# bucket is included on purpose: a slab whose grader you cannot identify is
# still a slab, and marking it as such keeps it out of the raw price pool.
GRADING_COMPANY_CHOICES = [[c, COMPANY_LABELS.get(c, c)] for c in COMPANY_ORDER]
from app.watcherbase import watcherbase
import app.watchersearch as watchersearch
import app.dashboard as dashboard
from app.download_manager import download_manager
from app.collection import (
    Collection, calculate_collection_price, calculate_collection_value,
    update_value_history, get_value_history, backfill_value_history,
    load_pages_for_collection
)
from app.sync import sync_manager
from app.rename import rename_page
from app.config import PAGES_DIR, ARCHIVE_DIR, IMAGES_DIR, CHANGES_DIR, DOWNLOADS_DIR, get_setting, load_settings
from app.scheduler import AutoRefreshScheduler, schedule_status

# Daily auto-refresh timer (started in __main__). Enqueues a full refresh via
# the download queue worker; gated by the auto_import_enabled setting.
auto_scheduler = AutoRefreshScheduler(lambda: download_manager.start())


def _import_downloads_if_manual():
    """Import freshly-saved HTML on page load, unless automation owns importing.

    When auto_import_enabled is on, the queue worker + scheduler handle imports,
    so the request path no longer blocks on import_all_pages().
    """
    if not get_setting('auto_import_enabled', False):
        watcherbase.import_all_pages()


def _resolve_search_params():
    """Resolve the search grid's sort/price params from the request, falling back
    to the user's configured display defaults (settings) when not in the URL."""
    settings = load_settings()
    sort_by = request.args.get('sortBy') or settings.get('default_sort_by', 'name')
    # Explicit ?order wins; then the configured default; finally a smart default
    # (ascending for name, descending for price-like fields).
    sort_order = (request.args.get('order')
                  or settings.get('default_sort_order')
                  or ('asc' if sort_by == 'name' else 'desc'))
    price_period = request.args.get('pricePeriod') or settings.get('default_price_period', 'last')
    price_type = request.args.get('priceType') or settings.get('default_price_type', 'available')
    return sort_by, sort_order, price_period, price_type


def _render_search_shell():
    """Render the client-side search grid shell, seeding CW_CFG with the user's
    configured display defaults so the grid opens on the right sort/price view
    when the URL omits those params (e.g. via the 'Browse all cards' link)."""
    settings = load_settings()
    return render_template(
        'search.htm',
        default_sort_by=settings.get('default_sort_by', 'name'),
        # Leave empty when unset so the client applies its smart order default.
        default_sort_order=settings.get('default_sort_order', ''),
        default_price_period=settings.get('default_price_period', 'last'),
        default_price_type=settings.get('default_price_type', 'available'),
    )

# Filter out noisy status endpoint from logs
class StatusFilter(logging.Filter):
    def filter(self, record):
        return '/api/download/status' not in record.getMessage()

app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'your_secret_key'
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

# Apply filter to suppress status endpoint logging
logging.getLogger('werkzeug').addFilter(StatusFilter())

@app.route('/', methods=['GET','POST'])
def cardwatcher():
    # methods for search site
    if request.args.get('searchString',''):
        print("cardwatcher | search: " +request.args.get('searchString',''))
        _import_downloads_if_manual()
        # Grid is rendered client-side (static/viewer/search.js) over the manifest
        # + price_history; the server just serves the shell.
        return _render_search_shell()

    # otherwise we're leaving the search site
    # first, check if the user requested a specifig page
    page_name = request.args.get('name','')
    print("cardwatcher | open page " + page_name)
    #download_page(page_name)
    # then check if a new page was downloaded and load that page
    # if the user specified a page, return that page instead


    _import_downloads_if_manual()
    # if we have a page in memory, load the specific site
    if page_name != "":
        # Handle archive/unarchive action
        if request.args.get('archive','') == 'toggle':
            print("cardwatcher | toggling archive status for: " + page_name)
            watcherbase.toggle_archive(page_name)
            return redirect(f'/?name={page_name}')

        page = watcherbase.get_page(page_name)
        if request.args.get('delete',''):
            print("cardwatcher | delete: " +request.args.get('delete',''))
            page.delete_listings([int(request.args.get('delete',''))])
            watcherbase.update_price_history_for_page(page)

        # Archive/unarchive individual listings
        if request.args.get('archive','') and request.args.get('archive','') != 'toggle':
            listing_idx = int(request.args.get('archive',''))
            print(f"cardwatcher | archive listing: {listing_idx}")
            page.archive_listing(listing_idx)
            watcherbase.update_price_history_for_page(page)
            return redirect(f'/?name={page_name}')

        if request.args.get('unarchive',''):
            listing_idx = int(request.args.get('unarchive',''))
            print(f"cardwatcher | unarchive listing: {listing_idx}")
            page.unarchive_listing(listing_idx)
            watcherbase.update_price_history_for_page(page)
            return redirect(f'/?name={page_name}')

        # Get collection items for this page
        canonical_name = page_name[:-5] if page_name.endswith('.json') else page_name
        collection = Collection().load()
        collection_items = collection.get_items_for_page(canonical_name)

        # Load price history data for this page
        price_info = {
            'available_avg': 0, 'available_change': 0,
            'sold_avg': 0, 'sold_change': 0,
            'lowest_price': 0
        }
        price_history_path = os.path.join(CHANGES_DIR, "price_history.json")
        if os.path.exists(price_history_path):
            try:
                with open(price_history_path, "r", encoding="utf-8") as f:
                    price_history = json.load(f)
                if canonical_name in price_history:
                    hist = price_history[canonical_name]
                    last = hist.get('last_download', {})
                    price_info['available_avg'] = round(last.get('avg', 0) or 0, 2)
                    price_info['available_change'] = round(last.get('avg_change', 0) or 0, 2)
                    price_info['sold_avg'] = round(last.get('ended_avg', 0) or 0, 2)
                    price_info['sold_change'] = round(last.get('ended_avg_change', 0) or 0, 2)
                    price_info['lowest_price'] = round(hist.get('current_min', 0) or 0, 2)
            except (json.JSONDecodeError, IOError):
                pass

        # Daily history of the three market-price methods for the price graph
        market_series = watcherbase.calculate_market_price_series(page)

        return render_template(
            'blanko.htm',
            table_content=page.build_table(),
            market_series=json.dumps(market_series),
            main_image = page.image,
            card_name=page.card,
            set_name = page.set,
            available = page.available,
            available_graded = page.available_graded,
            cardmarket_link="https://www.cardmarket.com/en/"+page.canonical_name.replace('_','/'),
            country_selection = page.build_country_selection(),
            language_selection = page.build_language_selection(),
            grading_selection = page.build_grading_selection(),
            grading_companies = GRADING_COMPANY_CHOICES,
            available_languages = page.languages,
            is_archived=page.isArchived,
            page_name=page_name,
            canonical_name=canonical_name,
            collection_items=collection_items,
            in_collection=len(collection_items) > 0,
            price_info=price_info)
    # No specific page requested. Show the full search grid only when explicitly
    # asked (Browse all / collection view); otherwise the landing is the dashboard.
    elif request.args.get('view') == 'search' or request.args.get('collection', '') == 'true':
        # Client-side grid (see above).
        return _render_search_shell()
    else:
        # Dashboard panels are rendered client-side (static/viewer/dashboard.js).
        return render_template('dashboard.htm')

@app.route('/api/download/start', methods=['POST'])
def start_download():
    result = download_manager.start()
    return jsonify(result)


@app.route('/api/download/stop', methods=['POST'])
def stop_download():
    result = download_manager.stop()
    return jsonify(result)


@app.route('/api/download/status', methods=['GET'])
def download_status():
    status = download_manager.get_status()
    return jsonify(status)


@app.route('/api/import/run', methods=['POST'])
def import_downloads():
    """Manually import any HTML saved into downloads/ (ad-hoc manual saves).

    Useful when auto_import_enabled is on and the page-load import is disabled.
    """
    report = watcherbase.import_all_pages()
    return jsonify({"success": True, "report": report})


@app.route('/api/schedule/status', methods=['GET'])
def schedule_status_api():
    """Auto-refresh schedule: enabled, last finished, next run (with relatives)."""
    return jsonify(schedule_status())


def _failed_imports():
    """Names of quarantined downloads waiting in downloads/failed/ (WP2)."""
    from app.config import FAILED_DIR
    if not os.path.isdir(FAILED_DIR):
        return []
    return sorted(f for f in os.listdir(FAILED_DIR) if f.lower().endswith('.htm'))


@app.route('/import', methods=['GET'])
def import_management():
    """Import-management page: queue status, schedule, refresh/import controls."""
    from app.config import load_settings
    return render_template('import.htm',
                           schedule=schedule_status(),
                           failed_imports=_failed_imports(),
                           settings=load_settings())


@app.route('/api/download/single', methods=['POST'])
def download_single():
    """Download a single page by name."""
    data = request.get_json()
    if not data or 'page_name' not in data:
        return jsonify({"success": False, "message": "Missing page_name parameter"})

    page_name = data['page_name']
    # Remove .json extension if present
    if page_name.endswith('.json'):
        page_name = page_name[:-5]

    result = download_manager.download_single_page(page_name)
    return jsonify(result)


@app.route('/api/download/url', methods=['POST'])
def download_from_url():
    """Download a page from a CardMarket URL."""
    data = request.get_json()
    if not data or 'url' not in data:
        return jsonify({"success": False, "message": "Missing url parameter"})

    result = download_manager.download_from_url(data['url'])
    return jsonify(result)


@app.route('/data/images/<path:filename>')
def serve_image(filename):
    return send_from_directory(IMAGES_DIR, filename)


@app.route('/data/price_history.json')
def serve_price_history():
    """Serve the precomputed metrics the client-side dashboard/search read."""
    return send_from_directory(CHANGES_DIR, 'price_history.json')


@app.route('/api/manifest', methods=['GET'])
def api_manifest():
    """Active + archived card index ({canonical, updated}) for the viewer JS."""
    import app.viewer as viewer
    return jsonify(viewer.build_manifest())


# Collection API routes
@app.route('/api/listing/grade', methods=['POST'])
def set_listing_grade():
    """Correct (or clear) the grade parsed out of a listing's comment.

    POST rather than a query-param link like archive/delete because it carries
    two values, and it matches the /api/collection/* style. The grade is stored
    with source 'manual' so the next import does not parse over it.
    """
    data = request.get_json()
    if not data or 'canonical_name' not in data or 'row' not in data:
        return jsonify({"success": False, "message": "Missing canonical_name or row"})

    canonical_name = data['canonical_name']
    if canonical_name.endswith('.json'):
        canonical_name = canonical_name[:-5]

    page = watcherbase.get_page(canonical_name + '.json')
    if not page:
        return jsonify({"success": False, "message": "Page not found"})

    company = (data.get('company') or '').strip().upper()
    grade = data.get('grade')
    if company and company not in COMPANY_ORDER:
        return jsonify({"success": False, "message": f"Unknown grading company: {company}"})
    if company and grade is None:
        return jsonify({"success": False, "message": "A grade is required"})

    if not page.set_listing_grade(int(data['row']), company, grade):
        return jsonify({"success": False, "message": "No such listing"})

    # Keep the stored metrics in step with the edit, exactly as the archive and
    # unarchive actions do -- the raw floor may have just changed.
    watcherbase.update_price_history_for_page(page)

    listing = page.listings[int(data['row'])]
    return jsonify({
        "success": True,
        "company": listing.grade_company,
        "grade": listing.grade,
        "label": listing.grade_label()
    })


@app.route('/api/page/rename', methods=['POST'])
def rename_page_route():
    """Point a tracked card at a new CardMarket URL after the product moved.

    POST rather than a query-param link like archive because it carries typed
    user input. The whole identity moves (page file, image, metrics, collection
    entries), so the caller has to reload under the returned page name.
    """
    data = request.get_json(silent=True) or {}
    if 'page_name' not in data or 'url' not in data:
        return jsonify({"success": False, "message": "Missing page_name or url"})

    return jsonify(rename_page(data['page_name'], data['url']))


@app.route('/api/collection', methods=['GET'])
def get_collection():
    """Get full collection with calculated prices."""
    collection = Collection().load()
    total_value, item_count, items_with_prices = calculate_collection_value(collection)

    items_data = []
    for item, price in items_with_prices:
        item_dict = item.to_dict()
        item_dict['calculated_price'] = round(price, 2)
        item_dict['total_value'] = round(price * item.quantity, 2)
        items_data.append(item_dict)

    return jsonify({
        "items": items_data,
        "total_value": round(total_value, 2),
        "item_count": item_count,
        "updated_at": collection.updated_at
    })


@app.route('/api/collection/add', methods=['POST'])
def add_to_collection():
    """Add item to collection."""
    data = request.get_json()
    if not data or 'canonical_name' not in data:
        return jsonify({"success": False, "message": "Missing canonical_name"})

    collection = Collection().load()
    item = collection.add_item(
        canonical_name=data['canonical_name'],
        condition=data.get('condition', 'NM'),
        language=data.get('language', 'English'),
        first_ed=data.get('first_ed', 0),
        reverse_holo=data.get('reverse_holo', 0),
        quantity=data.get('quantity', 1),
        added_at=data.get('added_at'),
        grade_company=data.get('grade_company', ''),
        grade=data.get('grade')
    )

    # Calculate price for the added item
    page = watcherbase.get_page(data['canonical_name'] + '.json')
    price = 0
    if page:
        price = calculate_collection_price(
            page, item.condition, item.language, item.first_ed, item.reverse_holo,
            item.grade_company, item.grade
        )

    return jsonify({
        "success": True,
        "item": item.to_dict(),
        "calculated_price": round(price, 2)
    })


@app.route('/api/collection/update', methods=['POST'])
def update_collection_item():
    """Update item quantity in collection."""
    data = request.get_json()
    if not data or 'canonical_name' not in data:
        return jsonify({"success": False, "message": "Missing canonical_name"})

    collection = Collection().load()
    success = collection.update_item(
        canonical_name=data['canonical_name'],
        condition=data.get('condition', 'NM'),
        language=data.get('language', 'English'),
        first_ed=data.get('first_ed', 0),
        reverse_holo=data.get('reverse_holo', 0),
        quantity=data.get('quantity', 1),
        grade_company=data.get('grade_company', ''),
        grade=data.get('grade')
    )

    return jsonify({"success": success})


@app.route('/api/collection/edit', methods=['POST'])
def edit_collection_item():
    """Edit an item's attributes in collection."""
    data = request.get_json()
    if not data or 'canonical_name' not in data:
        return jsonify({"success": False, "message": "Missing canonical_name"})

    collection = Collection().load()
    success = collection.edit_item(
        canonical_name=data['canonical_name'],
        old_condition=data.get('old_condition'),
        old_language=data.get('old_language'),
        old_first_ed=data.get('old_first_ed'),
        old_reverse_holo=data.get('old_reverse_holo'),
        new_condition=data.get('new_condition'),
        new_language=data.get('new_language'),
        new_first_ed=data.get('new_first_ed'),
        new_reverse_holo=data.get('new_reverse_holo'),
        new_quantity=data.get('new_quantity', 1),
        new_added_at=data.get('new_added_at'),
        old_grade_company=data.get('old_grade_company', ''),
        old_grade=data.get('old_grade'),
        new_grade_company=data.get('new_grade_company', ''),
        new_grade=data.get('new_grade')
    )

    return jsonify({"success": success})


@app.route('/api/collection/remove', methods=['POST'])
def remove_from_collection():
    """Remove item from collection."""
    data = request.get_json()
    if not data or 'canonical_name' not in data:
        return jsonify({"success": False, "message": "Missing canonical_name"})

    collection = Collection().load()
    removed = collection.remove_item(
        canonical_name=data['canonical_name'],
        condition=data.get('condition'),
        language=data.get('language'),
        first_ed=data.get('first_ed'),
        reverse_holo=data.get('reverse_holo'),
        grade_company=data.get('grade_company', ''),
        grade=data.get('grade')
    )

    return jsonify({
        "success": len(removed) > 0,
        "removed_count": len(removed)
    })


@app.route('/api/collection/value', methods=['GET'])
def get_collection_value():
    """Get total collection value."""
    collection = Collection().load()
    total_value, item_count, _ = calculate_collection_value(collection)

    # Update history with today's value
    update_value_history(collection)

    return jsonify({
        "total_value": round(total_value, 2),
        "item_count": item_count,
        "card_count": len(collection.get_canonical_names())
    })


@app.route('/api/collection/value-history', methods=['GET'])
def get_collection_value_history():
    """
    Get collection value history for graphing.

    Query params:
        period: '2w', '2m', '6m', or '1y' (default: '2m')
        mode: 'acquisition' or 'portfolio' (default: 'acquisition')
            - acquisition: shows value based on when items were added
            - portfolio: treats all items as always owned (pure market performance)

    Returns JSON:
    {
        "labels": ["2024-02-01", "2024-02-02", ...],
        "values": [1234.56, 1240.00, ...],
        "current_value": 1300.00,
        "change": 65.44,
        "change_percent": 5.3
    }
    """
    period = request.args.get('period', '2m')
    mode = request.args.get('mode', 'acquisition')
    if mode not in ('acquisition', 'portfolio'):
        mode = 'acquisition'

    period_days = {
        '2w': 14,
        '2m': 60,
        '6m': 180,
        '1y': 365
    }.get(period, 60)

    collection = Collection().load()

    # If collection is empty, return empty data
    if not collection.items:
        return jsonify({
            "labels": [],
            "values": [],
            "current_value": 0,
            "change": 0,
            "change_percent": 0
        })

    # Load pages once for all calculations
    pages_cache = load_pages_for_collection(collection)

    # Ensure today's value is recorded
    update_value_history(collection, pages_cache, mode=mode)

    # Select appropriate backfill marker based on mode
    backfilled_to = collection.history_backfilled_to if mode == 'acquisition' else collection.portfolio_backfilled_to

    # Check if backfill needed
    if not backfilled_to:
        backfill_value_history(collection, period_days, pages_cache, mode=mode)
    else:
        # Check if we need to backfill further
        from datetime import date, timedelta
        target_start = date.today() - timedelta(days=period_days)
        try:
            backfilled_date = date.fromisoformat(backfilled_to)
            if backfilled_date > target_start:
                backfill_value_history(collection, period_days, pages_cache, mode=mode)
        except (ValueError, TypeError):
            backfill_value_history(collection, period_days, pages_cache, mode=mode)

    # Get history data
    history = get_value_history(collection, period_days, mode=mode)

    if not history:
        return jsonify({
            "labels": [],
            "values": [],
            "current_value": 0,
            "change": 0,
            "change_percent": 0
        })

    labels = [h[0] for h in history]
    values = [h[1] for h in history]

    current = values[-1] if values else 0
    earliest = values[0] if values else 0
    change = current - earliest
    change_percent = (change / earliest * 100) if earliest > 0 else 0

    return jsonify({
        "labels": labels,
        "values": values,
        "current_value": round(current, 2),
        "change": round(change, 2),
        "change_percent": round(change_percent, 1)
    })


@app.route('/api/collection/for-page/<path:canonical_name>', methods=['GET'])
def get_collection_for_page(canonical_name):
    """Get collection items for a specific page."""
    # Remove .json extension if present
    if canonical_name.endswith('.json'):
        canonical_name = canonical_name[:-5]

    collection = Collection().load()
    items = collection.get_items_for_page(canonical_name)

    # Calculate prices for each item
    page = watcherbase.get_page(canonical_name + '.json')
    items_data = []
    for item in items:
        item_dict = item.to_dict()
        price = 0
        if page:
            price = calculate_collection_price(
                page, item.condition, item.language, item.first_ed, item.reverse_holo,
                item.grade_company, item.grade
            )
        item_dict['calculated_price'] = round(price, 2)
        item_dict['total_value'] = round(price * item.quantity, 2)
        items_data.append(item_dict)

    return jsonify({
        "items": items_data,
        "in_collection": len(items) > 0
    })


# Settings routes
@app.route('/settings')
def settings_page():
    """Render settings page."""
    from app.config import load_settings, COLLECTION_FILE
    settings = load_settings()
    return render_template('settings.htm', settings=settings, collection_file=COLLECTION_FILE)


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Get current settings."""
    from app.config import load_settings
    return jsonify(load_settings())


@app.route('/api/settings', methods=['POST'])
def save_settings_api():
    """Save settings."""
    from app.config import load_settings, save_settings
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "message": "No data provided"})

    # Load existing settings and update with new values
    settings = load_settings()
    restart_required = False

    # Track which settings require restart
    restart_keys = ['port', 'show_console', 'data_dir', 'collection_file']

    for key, value in data.items():
        if key in restart_keys and settings.get(key) != value:
            restart_required = True
        settings[key] = value

    save_settings(settings)

    return jsonify({
        "success": True,
        "restart_required": restart_required
    })


@app.route('/api/settings/browse-data-dir', methods=['POST'])
def browse_data_dir():
    """Open a folder browser dialog for selecting data directory."""
    import sys
    try:
        if sys.platform == 'win32':
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()  # Hide the main window
            root.wm_attributes('-topmost', 1)  # Bring dialog to front
            folder_path = filedialog.askdirectory(
                title="Select CardWatcher Data Directory"
            )
            root.destroy()
            if folder_path:
                return jsonify({"success": True, "path": folder_path})
            else:
                return jsonify({"success": False, "message": "No folder selected"})
        else:
            return jsonify({"success": False, "message": "Folder browser not supported on this platform. Please enter the path manually."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error opening folder browser: {str(e)}"})


@app.route('/api/settings/browse-collection-file', methods=['POST'])
def browse_collection_file():
    """Open a file browser dialog for selecting collection file."""
    import sys
    try:
        if sys.platform == 'win32':
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.wm_attributes('-topmost', 1)
            file_path = filedialog.asksaveasfilename(
                title="Select Collection File",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                initialfile=".cardwatcher_collection.json"
            )
            root.destroy()
            if file_path:
                return jsonify({"success": True, "path": file_path})
            else:
                return jsonify({"success": False, "message": "No file selected"})
        else:
            return jsonify({"success": False, "message": "File browser not supported on this platform. Please enter the path manually."})
    except Exception as e:
        return jsonify({"success": False, "message": f"Error opening file browser: {str(e)}"})


@app.route('/api/exit', methods=['POST'])
def exit_application():
    """Shut down the Flask application."""
    import os

    def shutdown():
        # Give time for response to be sent
        import time
        time.sleep(0.5)
        os._exit(0)

    # Start shutdown in background thread
    shutdown_thread = threading.Thread(target=shutdown)
    shutdown_thread.start()

    return jsonify({"success": True, "message": "Shutting down..."})


# Sync API routes
@app.route('/api/sync/status', methods=['GET'])
def sync_status():
    """Get sync availability and last sync info."""
    info = sync_manager.check_sync_available()
    last_sync = sync_manager.get_last_sync_info()
    return jsonify({**info, **last_sync})


@app.route('/api/sync/pull', methods=['POST'])
def sync_pull():
    """Pull latest data from remote (no push)."""
    result = sync_manager.pull_only()
    return jsonify(result)


@app.route('/api/sync/full', methods=['POST'])
def sync_full():
    """Full sync: pull and push changes."""
    result = sync_manager.full_sync()
    return jsonify(result)


def _apply_console_visibility():
    """Hide the console window when the show_console setting is off.

    The PyInstaller build ships with console=True, so the packaged app always
    has its own console window; honour the setting by hiding it at startup.
    Only meaningful on Windows for the frozen exe — when run as a script the
    console is the user's own terminal, so we leave it alone.
    """
    import sys
    from app.config import IS_FROZEN
    if sys.platform != 'win32' or not IS_FROZEN:
        return
    if get_setting('show_console', True):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def is_port_available(port):
    """Check if a port is available for binding."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False


def find_available_port(start_port, max_attempts=10):
    """Find an available port starting from start_port."""
    for offset in range(max_attempts):
        port = start_port + offset
        if is_port_available(port):
            return port
    return None


if __name__ == '__main__':
    # Args were parsed and paths initialized in _early_init() above
    args = _startup_args

    # Hide the console window if the user disabled it (frozen Windows build).
    _apply_console_visibility()

    # Ensure data directories exist
    for d in [PAGES_DIR, ARCHIVE_DIR, IMAGES_DIR, CHANGES_DIR, DOWNLOADS_DIR]:
        os.makedirs(d, exist_ok=True)

    # Find an available port
    port = args.port
    if not is_port_available(port):
        print(f"Port {port} is in use, looking for an available port...")
        port = find_available_port(port + 1)
        if port is None:
            print(f"Could not find an available port. Please close other applications or specify a different port with -p.")
            exit(1)
        print(f"Using port {port}")

    # Start the daily auto-refresh timer (no-ops unless auto_import_enabled).
    auto_scheduler.start()

    # Open browser automatically after short delay
    if not args.no_browser:
        url = f'http://localhost:{port}'
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    app.run(host='0.0.0.0', port=port)


