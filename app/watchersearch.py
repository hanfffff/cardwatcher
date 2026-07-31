from datetime import datetime
import os
import json
from app.config import PAGES_DIR, ARCHIVE_DIR, CHANGES_DIR
from app.collection import calculate_collection_price, Collection
from app.page import Page

def build_search(search_term="", sort_by="name", sort_order="asc", price_period="last", price_type="available", collection=None):
    """
    Build the search view HTML.

    Args:
        search_term: Filter pages by name
        sort_by: Sort field (name, price, priceChange, percentChange)
        sort_order: Sort direction (asc, desc)
        price_period: Price comparison period (last, 1w, 1m, 2m, 6m)
        price_type: Price type for sorting (available, sold)
        collection: Optional Collection object to filter by (only show cards in collection)
    """
    # Get collection canonical names for filtering
    collection_names = collection.get_canonical_names() if collection else None

    # Always load all collection names to show indicator on cards in collection
    all_collection_names = set()
    if collection:
        all_collection_names = collection_names
    else:
        # Load collection just for the indicator badges
        all_collection = Collection().load()
        all_collection_names = all_collection.get_canonical_names()

    # Build a map of canonical_name -> list of collection items for price calculation
    collection_items_map = {}
    if collection:
        for item in collection.items:
            if item.canonical_name not in collection_items_map:
                collection_items_map[item.canonical_name] = []
            collection_items_map[item.canonical_name].append(item)
    # Load unified price history (contains all metrics including last_download data)
    price_history = {}
    if os.path.exists(os.path.join(CHANGES_DIR, "price_history.json")):
        try:
            with open(os.path.join(CHANGES_DIR, "price_history.json"), "r", encoding="utf-8") as f:
                price_history = json.load(f)
        except (json.JSONDecodeError, IOError):
            price_history = {}

    # Build data structure with all sort-relevant information
    file_list = [f for f in os.listdir(PAGES_DIR) if f.endswith('.json')]
    file_data_list = []

    search_terms = search_term.lower().split() if search_term else []
    for file_name in file_list:
        # if there is a search term, all words in the term have to be in the canonical name
        if search_term and any([term not in file_name.lower() for term in search_terms]):
            continue

        canonical_name = file_name[:-5]

        # If collection filter is active, only show cards in collection
        if collection_names is not None and canonical_name not in collection_names:
            continue

        timestamp = os.path.getmtime(os.path.join(PAGES_DIR, file_name))

        # Read available quantity directly from page JSON — always current, never stale
        page_available = 0
        try:
            with open(os.path.join(PAGES_DIR, file_name), 'r', encoding='utf-8') as pf:
                pdata = json.load(pf)
            page_available = pdata.get('available', 0) or 0
        except (IOError, json.JSONDecodeError):
            pass

        # Extract price data for sorting based on selected period
        price_avg = 0.0
        price_chg = 0.0
        percent_chg = 0.0
        price_min = 0.0
        # Ended (sold) prices
        ended_avg = 0.0
        ended_chg = 0.0
        ended_percent_chg = 0.0

        # Get lowest price from price_history (available for all periods)
        if canonical_name in price_history:
            price_min = price_history[canonical_name].get('current_min', 0) or 0

        if price_period == "last":
            # Use last download comparison from price_history
            if canonical_name in price_history:
                last = price_history[canonical_name].get('last_download', {})
                price_avg = last.get('avg', 0) or 0
                price_chg = last.get('avg_change', 0) or 0
                if price_avg > 0:
                    percent_chg = (price_chg / price_avg) * 100
                # Ended prices
                ended_avg = last.get('ended_avg', 0) or 0
                ended_chg = last.get('ended_avg_change', 0) or 0
                if ended_avg > 0:
                    ended_percent_chg = (ended_chg / ended_avg) * 100
        else:
            # Use period-based comparison
            if canonical_name in price_history:
                hist = price_history[canonical_name]
                price_avg = hist.get('current_avg', 0) or 0
                period_data = hist.get(price_period, {})
                if period_data and period_data.get('change') is not None:
                    price_chg = period_data['change']
                if price_avg > 0:
                    percent_chg = (price_chg / price_avg) * 100
                # Ended prices
                ended_avg = hist.get('current_ended_avg', 0) or 0
                if period_data and period_data.get('ended_change') is not None:
                    ended_chg = period_data['ended_change']
                if ended_avg > 0:
                    ended_percent_chg = (ended_chg / ended_avg) * 100

        # Calculate collection price if in collection view
        collection_price = 0.0
        collection_qty = 0
        collection_unit_price = 0.0  # Average unit price across all items
        if collection and canonical_name in collection_items_map:
            # Load the page to calculate collection prices
            page = Page()
            page_path = os.path.join(PAGES_DIR, file_name)
            if os.path.exists(page_path):
                page.import_page(page_path)
                for item in collection_items_map[canonical_name]:
                    item_price = calculate_collection_price(
                        page, item.condition, item.language, item.first_ed, item.reverse_holo,
                        item.grade_company, item.grade
                    )
                    collection_price += item_price * item.quantity
                    collection_qty += item.quantity
                # Calculate average unit price
                if collection_qty > 0:
                    collection_unit_price = collection_price / collection_qty

        # Compute market metrics (drainage, inflation, net supply)
        current_available = page_available
        ins = sld = 0
        if canonical_name in price_history:
            if price_period == 'last':
                last_dl = price_history[canonical_name].get('last_download', {})
                ins = last_dl.get('inserted', 0) or 0
                sld = last_dl.get('sold', 0) or 0
            else:
                pd = price_history[canonical_name].get(price_period, {})
                ins = pd.get('listings_added', 0) or 0
                sld = pd.get('listings_removed', 0) or 0
        base = current_available - ins + sld
        drainage_pct  = round(sld / base * 100, 1) if base > 0 else None
        inflation_pct = round(ins / base * 100, 1) if base > 0 else None
        net_supply_pct = round((ins - sld) / base * 100, 1) if base > 0 else None

        # Buy-now floor (market price, dominant language + average condition) and
        # the period-aware change in both "From" (raw lowest) and "Floor".
        market_floor = 0.0
        from_change = None
        floor_change = None
        if canonical_name in price_history:
            ph = price_history[canonical_name]
            market_floor = (ph.get('market') or {}).get('floor', 0) or 0
            cur_min = ph.get('current_min', 0) or 0
            if price_period == 'last':
                ld = ph.get('last_download', {})
                from_change = ld.get('min_change')
                floor_change = ld.get('floor_change')
            else:
                pdp = ph.get(price_period, {})
                hist_min = pdp.get('historical_min')
                if hist_min:
                    from_change = round(cur_min - hist_min, 2)
                hist_floor = (pdp.get('market') or {}).get('floor')
                if hist_floor:
                    floor_change = round(market_floor - hist_floor, 2)

        file_data_list.append({
            'file_name': file_name,
            'canonical_name': canonical_name,
            'timestamp': timestamp,
            'price_avg': price_avg,
            'price_chg': price_chg,
            'percent_chg': percent_chg,
            'price_min': price_min,
            'ended_avg': ended_avg,
            'ended_chg': ended_chg,
            'ended_percent_chg': ended_percent_chg,
            'collection_price': collection_price,
            'collection_qty': collection_qty,
            'collection_unit_price': collection_unit_price,
            'in_collection': canonical_name in all_collection_names,
            'current_available': current_available,
            'drainage_pct': drainage_pct,
            'inflation_pct': inflation_pct,
            'net_supply_pct': net_supply_pct,
            'market_floor': market_floor,
            'from_change': from_change,
            'floor_change': floor_change,
        })

    # Apply sorting based on parameters and price type
    # When sorting by price fields, use either available or sold prices based on price_type
    if sort_by == "price":
        if price_type == "sold":
            file_data_list.sort(key=lambda x: x['ended_avg'], reverse=(sort_order == "desc"))
        else:
            file_data_list.sort(key=lambda x: x['price_avg'], reverse=(sort_order == "desc"))
    elif sort_by == "priceChange":
        if price_type == "sold":
            file_data_list.sort(key=lambda x: x['ended_chg'], reverse=(sort_order == "desc"))
        else:
            file_data_list.sort(key=lambda x: x['price_chg'], reverse=(sort_order == "desc"))
    elif sort_by == "percentChange":
        if price_type == "sold":
            file_data_list.sort(key=lambda x: x['ended_percent_chg'], reverse=(sort_order == "desc"))
        else:
            file_data_list.sort(key=lambda x: x['percent_chg'], reverse=(sort_order == "desc"))
    elif sort_by == "lowestPrice":
        file_data_list.sort(key=lambda x: x['price_min'], reverse=(sort_order == "desc"))
    elif sort_by == "drainage":
        file_data_list.sort(key=lambda x: x['drainage_pct'] if x['drainage_pct'] is not None else -1, reverse=(sort_order == "desc"))
    elif sort_by == "inflation":
        file_data_list.sort(key=lambda x: x['inflation_pct'] if x['inflation_pct'] is not None else -1, reverse=(sort_order == "desc"))
    elif sort_by == "netSupply":
        file_data_list.sort(key=lambda x: x['net_supply_pct'] if x['net_supply_pct'] is not None else 0, reverse=(sort_order == "desc"))
    elif sort_by == "collectionPrice":
        # Sort by total collection value for this card
        file_data_list.sort(key=lambda x: x['collection_price'], reverse=(sort_order == "desc"))
    else:  # default to name
        file_data_list.sort(key=lambda x: x['file_name'], reverse=(sort_order == "desc"))

    # Generate HTML from sorted data
    search = ""
    for data in file_data_list:
        file_name = data['file_name']
        canonical_name = data['canonical_name']
        timestamp = data['timestamp']
        # Get canonical name (strip .json extension) for lookup
        canonical_name = file_name[:-5]

        # Build availability change badges based on selected period
        availability_badges = ""
        badge_parts = []

        # Get current available count directly from page JSON (always current)
        current_available = data['current_available']

        if price_period == "last":
            # Use last download comparison from price_history
            if canonical_name in price_history:
                last = price_history[canonical_name].get('last_download', {})
                inserted = last.get('inserted', 0)
                sold = last.get('sold', 0)
                if inserted > 0:
                    badge_parts.append(f'<span style="color: rgb(34,139,34); font-weight: bold;">+{inserted}</span>')
                if sold > 0:
                    badge_parts.append(f'<span style="color: rgb(220, 20, 60); font-weight: bold;">-{sold}</span>')
        else:
            # Use period-based comparison from price_history
            if canonical_name in price_history:
                hist = price_history[canonical_name]
                period_data = hist.get(price_period, {})
                listings_added = period_data.get('listings_added', 0)
                listings_removed = period_data.get('listings_removed', 0)
                if listings_added > 0:
                    badge_parts.append(f'<span style="color: rgb(34,139,34); font-weight: bold;">+{listings_added}</span>')
                if listings_removed > 0:
                    badge_parts.append(f'<span style="color: rgb(220, 20, 60); font-weight: bold;">-{listings_removed}</span>')

        # Build badge with count + changes + net supply
        ns_pct = data['net_supply_pct']
        ns_badge_html = ""
        if ns_pct is not None:
            ns_color = "rgb(34,139,34)" if ns_pct > 0 else ("rgb(220,53,69)" if ns_pct < 0 else "#888")
            ns_sign = "+" if ns_pct > 0 else ""
            ns_badge_html = f'<div style="text-align:right;color:{ns_color};font-weight:bold;font-size:0.9em;">{ns_sign}{ns_pct}%</div>'
        if current_available > 0 or badge_parts or ns_badge_html:
            count_part = f'<span style="font-weight: bold;">{current_available}</span>' if current_available > 0 else ''
            separator = ' ' if count_part and badge_parts else ''
            changes_row = count_part + separator + ' '.join(badge_parts)
            inner_html = (f'<div style="display:flex;gap:6px;">{changes_row}</div>' if changes_row else '') + ns_badge_html
            availability_badges = '<div style="position: absolute; top: 4px; right: 4px; background: var(--cw-pill-strong); padding: 2px 6px; border-radius: 4px; font-size: 0.8em;">' + inner_html + '</div>'
        price_string = "--€ (0€)"
        price_arrow = ""
        price_style = "font-size: 0.85em; font-weight: bold; background: var(--cw-pill); padding: 2px 4px; border-radius: 4px; display: inline-block;"

        # Determine price_average and price_change based on selected period
        price_average = 0.0
        price_change = 0.0
        has_price_data = False

        if price_period == "last":
            # Use last download comparison from price_history
            if canonical_name in price_history:
                last = price_history[canonical_name].get('last_download', {})
                price_average = last.get('avg', 0) or 0
                price_change = last.get('avg_change', 0) or 0
                has_price_data = price_average > 0
        else:
            # Use period-based comparison from price_history
            if canonical_name in price_history:
                hist = price_history[canonical_name]
                price_average = hist.get('current_avg', 0) or 0
                period_data = hist.get(price_period, {})
                if period_data and period_data.get('change') is not None:
                    price_change = period_data['change']
                    has_price_data = True
                elif price_average > 0:
                    # We have current avg but no historical data for this period
                    price_change = 0
                    has_price_data = True

        if has_price_data and price_average > 0:
            sign = '+' if price_change >= 0 else ''
            percentage_change = (price_change / price_average) * 100

            # Add arrow indicator based on price change
            if price_change > 0:
                price_arrow = " ↑"
            elif price_change < 0:
                price_arrow = " ↓"
            else:
                price_arrow = " →"

            # Show percentage when sorting by percentage, otherwise show absolute change
            price_average = round(price_average,2) if price_average < 1000 else int(price_average)
            if sort_by == "percentChange":
                price_string = f"Avail: {price_average}€ ({sign}{round(percentage_change,1)}%){price_arrow}"
            else:
                price_change = round(price_change,2) if (price_average < 1000 or price_change < 1) else int(price_change)
                price_string = f"Avail: {price_average}€ ({sign}{price_change}€){price_arrow}"

            # Add color styling: green for price increase (good), red for price decrease
            if price_change > 0:
                # Price went up - green
                price_style = "font-size: 0.85em; color: rgb(34,139,34); font-weight: bold; background: var(--cw-pill); padding: 2px 4px; border-radius: 4px; display: inline-block;"
            elif price_change < 0:
                # Price went down - red
                price_style = "font-size: 0.85em; color: rgb(220, 20, 60); font-weight: bold; background: var(--cw-pill); padding: 2px 4px; border-radius: 4px; display: inline-block;"
            else:
                # No change - neutral with backdrop
                price_style = "font-size: 0.85em; font-weight: bold; background: var(--cw-pill); padding: 2px 4px; border-radius: 4px; display: inline-block;"

        # Determine ended (sold) price_average and price_change based on selected period
        ended_price_string = ""
        ended_price_style = "font-size: 0.85em; font-weight: bold; background: var(--cw-pill-gray); padding: 2px 4px; border-radius: 4px; display: inline-block;"
        ended_price_average = 0.0
        ended_price_change = 0.0
        has_ended_price_data = False

        if price_period == "last" and canonical_name in price_history:
            # Use last download comparison for ended prices
            last = price_history[canonical_name].get('last_download', {})
            ended_price_average = last.get('ended_avg', 0) or 0
            ended_price_change = last.get('ended_avg_change', 0) or 0
            has_ended_price_data = ended_price_average > 0
        elif price_period != "last" and canonical_name in price_history:
            hist = price_history[canonical_name]
            ended_price_average = hist.get('current_ended_avg', 0) or 0
            period_data = hist.get(price_period, {})
            if period_data and period_data.get('ended_change') is not None:
                ended_price_change = period_data['ended_change']
                has_ended_price_data = True
            elif ended_price_average > 0:
                ended_price_change = 0
                has_ended_price_data = True

        if has_ended_price_data and ended_price_average > 0:
            ended_sign = '+' if ended_price_change >= 0 else ''
            ended_percentage_change = (ended_price_change / ended_price_average) * 100

        if ended_price_change > 0:
            ended_arrow = " ↑"
        elif ended_price_change < 0:
            ended_arrow = " ↓"
        else:
            ended_arrow = " →"

        # Show percentage when sorting by percentage, otherwise show absolute change
        if has_ended_price_data:
            ended_price_average = round(ended_price_average,2) if ended_price_average < 1000 else int(ended_price_average)
            if sort_by == "percentChange":
                ended_price_string = f"Sold: {ended_price_average}€ ({ended_sign}{round(ended_percentage_change,1)}%){ended_arrow}"
            else:
                ended_price_change = round(ended_price_change,2) if ended_price_average < 1000 else int(ended_price_change)
                ended_price_string = f"Sold: {ended_price_average}€ ({ended_sign}{ended_price_change}€){ended_arrow}"
        else:
            if sort_by == "percentChange":
                ended_price_string = f"Sold: --"
            else:
                ended_price_string = f"Sold: --"

        if ended_price_change > 0:
            ended_price_style = "font-size: 0.85em; color: rgb(34,139,34); font-weight: bold; background: var(--cw-pill-gray); padding: 2px 4px; border-radius: 4px; display: inline-block;"
        elif ended_price_change < 0:
            ended_price_style = "font-size: 0.85em; color: rgb(220, 20, 60); font-weight: bold; background: var(--cw-pill-gray); padding: 2px 4px; border-radius: 4px; display: inline-block;"

        article_name = file_name[:-5].split('_')[-1].replace('-',' ')

        # Build ended price HTML if available
        ended_price_html = ""
        if ended_price_string:
            ended_price_html = "<div style=\"" + ended_price_style + "\">" + ended_price_string + "</div>"

        # Build "From" (raw lowest) and "Floor" (filtered buy-now) as their own
        # rows with change + arrow, mirroring the Avail/Sold rows. Decimals are
        # dropped on larger values to keep each row on a single line.
        def _compact(v):
            if abs(v) >= 100:
                return str(int(round(v)))
            r = round(v, 2)
            return str(int(r)) if r == int(r) else str(r)
        def price_row(label, value, change):
            if value <= 0:
                return ""
            base = "font-size: 0.78em; font-weight: bold; background: var(--cw-pill-blue); padding: 2px 4px; border-radius: 4px; display: inline-block; white-space: nowrap;"
            if change is None:
                return f'<div style="{base}">{label}: {_compact(value)}€</div>'
            sign = '+' if change >= 0 else ''
            arrow = ' ↑' if change > 0 else (' ↓' if change < 0 else ' →')
            color = 'rgb(34,139,34)' if change > 0 else ('rgb(220,20,60)' if change < 0 else '#555')
            colored = base if change == 0 else f"font-size: 0.78em; font-weight: bold; color: {color}; background: var(--cw-pill-blue); padding: 2px 4px; border-radius: 4px; display: inline-block; white-space: nowrap;"
            return f'<div style="{colored}">{label}: {_compact(value)}€ ({sign}{_compact(change)}€){arrow}</div>'

        lowest_price_html = (
            price_row("From", data['price_min'], data['from_change'])
            + price_row("Floor", data['market_floor'], data['floor_change'])
        )

        # Build market metrics HTML
        market_metrics_html = ""
        dr = data['drainage_pct']
        inf = data['inflation_pct']
        if sort_by in ('drainage', 'inflation', 'netSupply') and dr is not None:
            market_metrics_html += f'<div style="font-size:0.78em;color:rgb(220,53,69);">Drainage: {dr}%</div>'
            market_metrics_html += f'<div style="font-size:0.78em;color:rgb(34,139,34);">Inflation: {inf}%</div>'

        # Build collection price HTML (only when in collection view)
        collection_price_html = ""
        if collection and data['collection_price'] > 0:
            coll_total = round(data['collection_price'], 2)
            coll_qty = data['collection_qty']
            coll_unit = round(data['collection_unit_price'], 2)
            collection_price_html = f'<div style="font-size: 0.85em; font-weight: bold; color: #28a745; background: rgba(40,167,69,0.15); padding: 4px 8px; border-radius: 4px; display: inline-block;">{coll_qty}x @ {coll_unit}€ = {coll_total}€</div>'

        # In collection view, show collection price instead of regular prices
        if collection:
            search += "<div class=\"d-flex mb-4 col-12 col-sm-6 col-md-4 col-lg-2\">" + \
                        "<a name=\"" + file_name + "\" href=\"?name="+file_name+"&collection=true\" class=\"card text-center w-100 galleryBox\" style=\"position: relative;\">" + \
                            availability_badges + \
                            "<img src=\"data/images/" + file_name[:-5] +".jpg" + \
                            "\" alt=\"" + article_name + "\" class=\"lazy card-img-top img-fluid\">" + \
                            "<div class=\"card-body d-flex flex-column p-2\" style=\"gap: 2px;\">" + \
                                "<div class=\"card-title\" style=\"font-size: 0.9em; font-weight: bold; margin-bottom: 2px;\">" + \
                                    article_name + \
                                "</div>" + \
                                "<div style=\"font-size: 0.75em; color: #666;\">(" + \
                                    datetime.fromtimestamp(float(timestamp)).strftime('%d.%m.%Y') + \
                                ")</div>" + \
                                collection_price_html + \
                            "</div>" + \
                        "</a>" + \
                      "</div>"
        else:
            # Collection indicator badge (heart icon in top-left corner)
            collection_badge = ""
            if data['in_collection']:
                collection_badge = '<div style="position: absolute; top: 4px; left: 4px; background: rgba(40,167,69,0.9); color: white; padding: 2px 6px; border-radius: 4px; font-size: 0.8em;" title="In Collection">&#9829;</div>'

            search += "<div class=\"d-flex mb-4 col-12 col-sm-6 col-md-4 col-lg-2\">" + \
                        "<a name=\"" + file_name + "\" href=\"?name="+file_name+"\" class=\"card text-center w-100 galleryBox\" style=\"position: relative;\">" + \
                            collection_badge + \
                            availability_badges + \
                            "<img src=\"data/images/" + file_name[:-5] +".jpg" + \
                            "\" alt=\"" + article_name + "\" class=\"lazy card-img-top img-fluid\">" + \
                            "<div class=\"card-body d-flex flex-column p-2\" style=\"gap: 2px;\">" + \
                                "<div class=\"card-title\" style=\"font-size: 0.9em; font-weight: bold; margin-bottom: 2px;\">" + \
                                    article_name + \
                                "</div>" + \
                                "<div style=\"font-size: 0.75em; color: #666;\">(" + \
                                    datetime.fromtimestamp(float(timestamp)).strftime('%d.%m.%Y') + \
                                ")</div>" + \
                                "<div style=\"" + price_style + "\">" + \
                                   price_string + \
                                "</div>" + \
                                ended_price_html + \
                                lowest_price_html + \
                                market_metrics_html + \
                            "</div>" + \
                        "</a>" + \
                      "</div>"
    # Don't show archive section when in collection view
    if collection:
        return search

    search += "<h1 class=\"page-header\">Archive</h1>"
    file_list = [f for f in os.listdir(ARCHIVE_DIR) if f.endswith('.json')]
    file_info_list = [(file_name, os.path.getmtime(os.path.join(ARCHIVE_DIR,file_name))) for file_name in file_list]
    sorted_file_list = sorted(file_info_list, key=lambda x: x[0])
    for file_name,timestamp in sorted_file_list:
        if search_term and search_term.lower() not in file_name.lower():
            continue
        article_name = file_name[:-5].split('_')[-1].replace('-',' ')
        search += "<div class=\"d-flex mb-4 col-12 col-sm-6 col-md-4 col-lg-2\">" + \
                    "<a name=\"" + file_name + "\" href=\"?name="+file_name+"\" class=\"card text-center w-100 galleryBox\">" + \
                        "<img src=\"data/images/" + file_name[:-5] +".jpg" + \
                        "\" alt=\"" + article_name + "\" class=\"lazy card-img-top img-fluid\">" + \
                        "<div class=\"card-body d-flex flex-column p-2\" style=\"gap: 2px;\">" + \
                            "<div class=\"card-title\" style=\"font-size: 0.9em; font-weight: bold; margin-bottom: 2px;\">" + \
                                article_name + \
                            "</div>" + \
                            "<div style=\"font-size: 0.75em; color: #666;\">(" + \
                                datetime.fromtimestamp(float(timestamp)).strftime('%d.%m.%Y') + \
                            ")</div>" + \
                        "</div>" + \
                    "</a>" + \
                  "</div>"
    return search