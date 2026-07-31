"""
Dashboard builder — the landing page.

Reads the precomputed metrics in price_history.json (populated during import by
watcherbase.calculate_all_period_averages, including the three market-price
methods) and renders three signal panels:

- Biggest price movers   : largest 1-week % change in the market floor.
- Net supply loss        : cards whose available supply shrank most this week.
- Pressure / divergence  : where price and supply changes disagree (potential
                           early signals).

All ranking uses the market floor ('floor') as the canonical number and filters
out illiquid cards (see thresholds below).
"""

import os
import json
from app.config import PAGES_DIR, CHANGES_DIR

# The canonical "market value" every panel ranks and displays. The floor (low
# band of current asks) rather than the blend: the blend carries a realized-sales
# term, so one outlier sale — a graded/PSA copy going for a multiple of the raw
# price — drags it up sharply while the actual ask floor has not moved at all.
# That surfaced as phantom movers. The floor only moves when sellers reprice, and
# it is also what the browse view shows, so the two now agree.
MARKET_VALUE = 'floor'

# --- Tunable thresholds ---------------------------------------------------
MIN_AVAILABLE = 10        # ignore cards with fewer than this many available now
MIN_BASE = 10             # ignore thin markets for supply/pressure (start-of-week supply)
TOP_N = 8                 # rows per panel column

# Pressure / divergence buckets (percent)
SUPPLY_DROP = 15          # supply shrank at least this much -> "coiling" candidate
PRICE_FLAT = 5            # price moved less than this -> considered flat
PRICE_UP = 8              # price rose at least this much -> a real move
SUPPLY_GROW = 10          # supply grew at least this much -> sellers piling in


def _card_meta(canonical_name):
    """Display fields for a price_history key (which is a canonical name)."""
    return {
        'file': canonical_name + '.json',
        'name': canonical_name.split('_')[-1].replace('-', ' '),
        'image': 'data/images/' + canonical_name + '.jpg',
    }


def _tint(color, alpha='0.12'):
    """Build a faint translucent version of an rgb()/hex accent for backgrounds."""
    color = color.strip()
    if color.startswith('rgb(') and color.endswith(')'):
        return 'rgba(' + color[4:-1] + ',' + alpha + ')'
    return 'rgba(0,0,0,0.05)'


def _rows(entries):
    """Render a list of {canonical, primary, secondary, color} into card HTML."""
    html = ''
    for e in entries:
        meta = _card_meta(e['canonical'])
        color = e.get('color', '#444')
        tint = _tint(color)
        html += (
            '<a href="?name=' + meta['file'] + '" class="dash-card" '
            'style="--accent:' + color + ';--tint:' + tint + ';">'
            '<img src="' + meta['image'] + '" alt="" class="lazy dash-thumb">'
            '<div class="dash-info">'
            '<div class="dash-primary">' + e['primary'] + '</div>'
            '<div class="dash-name">' + meta['name'] + '</div>'
            '<div class="dash-secondary">' + e['secondary'] + '</div>'
            '</div></a>'
        )
    return html or '<div class="dash-empty">No cards meet the criteria yet.</div>'


def _fmt_eur(v):
    if not v:
        return '--€'
    return (str(int(v)) if v >= 1000 else str(round(v, 2))) + '€'


def _market_value(container):
    """The canonical market value out of a price_history entry or period dict."""
    return ((container or {}).get('market') or {}).get(MARKET_VALUE) or 0


def build_dashboard():
    """Return {'movers_html', 'supply_html', 'pressure_html'} for the template."""
    price_history = {}
    ph_path = os.path.join(CHANGES_DIR, 'price_history.json')
    if os.path.exists(ph_path):
        try:
            with open(ph_path, 'r', encoding='utf-8') as f:
                price_history = json.load(f)
        except (json.JSONDecodeError, IOError):
            price_history = {}

    # Only consider cards that still have an active page file.
    active_pages = {f[:-5] for f in os.listdir(PAGES_DIR) if f.endswith('.json')}

    movers = []        # (pct, canonical, value_now, value_1w)
    supply = []        # (net_pct, canonical, added, removed, base, value_now)
    pressure_rows = [] # dict per card with price_pct + net_pct for bucketing

    for canonical, entry in price_history.items():
        if canonical not in active_pages:
            continue
        value_now = _market_value(entry)
        available = entry.get('current_available') or 0
        wk = entry.get('1w') or {}
        value_1w = _market_value(wk)

        price_pct = None
        if value_now > 0 and value_1w > 0 and available >= MIN_AVAILABLE:
            price_pct = (value_now - value_1w) / value_1w * 100
            movers.append((price_pct, canonical, value_now, value_1w))

        base = wk.get('historical_available') or 0
        net_pct = None
        if base >= MIN_BASE:
            # Net item change over the week (same quantity the sparkline plots),
            # so the % badge and the graph agree. Fall back to gross flow.
            net_items = wk.get('available_change')
            if net_items is None:
                net_items = (wk.get('listings_added') or 0) - (wk.get('listings_removed') or 0)
            net_pct = net_items / base * 100
            if net_pct < 0:
                supply.append((net_pct, canonical, base, available, value_now))

        if price_pct is not None and net_pct is not None:
            pressure_rows.append({
                'canonical': canonical, 'price_pct': price_pct,
                'net_pct': net_pct, 'value_now': value_now,
            })

    # --- Movers: top risers and fallers, shown side by side ---------------
    movers.sort(key=lambda x: x[0], reverse=True)
    risers = movers[:TOP_N]
    fallers = sorted(movers[-TOP_N:], key=lambda x: x[0]) if len(movers) > TOP_N else []
    # avoid showing the same card as both when the list is short
    riser_names = {c for _, c, _, _ in risers}
    fallers = [f for f in fallers if f[1] not in riser_names][:TOP_N]

    def mover_row(pct, canonical, now, prev):
        sign = '+' if pct >= 0 else ''
        color = 'rgb(34,139,34)' if pct > 0 else ('rgb(220,53,69)' if pct < 0 else '#666')
        arrow = ' ↑' if pct > 0 else (' ↓' if pct < 0 else '')
        return {
            'canonical': canonical, 'color': color,
            'primary': f'{sign}{round(pct,1)}%{arrow}',
            'secondary': f'{_fmt_eur(prev)} → {_fmt_eur(now)}',
        }

    movers_html = (
        '<div class="dash-col"><h3 class="dash-subhead">▲ Gainers</h3>'
        + _rows([mover_row(*m) for m in risers]) + '</div>'
        '<div class="dash-col"><h3 class="dash-subhead">▼ Fallers</h3>'
        + _rows([mover_row(*m) for m in fallers]) + '</div>'
    )

    # --- Net supply loss --------------------------------------------------
    supply.sort(key=lambda x: x[0])  # most negative first
    def supply_row(net_pct, canonical, base, cur_avail, value_now):
        return {
            'canonical': canonical, 'color': 'rgb(220,53,69)',
            'primary': f'{round(net_pct,1)}% supply',
            'secondary': f'{base}→{cur_avail} avail · {_fmt_eur(value_now)}',
        }
    supply_html = _rows([supply_row(*s) for s in supply[:TOP_N]])

    # --- Pressure / divergence -------------------------------------------
    coiling, overbought, cooling = [], [], []
    for r in pressure_rows:
        pp, np = r['price_pct'], r['net_pct']
        if np <= -SUPPLY_DROP and abs(pp) < PRICE_FLAT:
            coiling.append(r)            # supply drained hard, price hasn't reacted
        elif pp >= PRICE_UP and np >= SUPPLY_GROW:
            cooling.append(r)            # price up but sellers piling in
        elif pp >= PRICE_UP and np > -PRICE_FLAT:
            overbought.append(r)         # price up without supply shrinking

    coiling.sort(key=lambda r: r['net_pct'])               # strongest drain first
    overbought.sort(key=lambda r: r['price_pct'], reverse=True)
    cooling.sort(key=lambda r: r['price_pct'], reverse=True)

    def pressure_row(r, color):
        return {
            'canonical': r['canonical'], 'color': color,
            'primary': f"price {('+' if r['price_pct']>=0 else '')}{round(r['price_pct'],1)}%",
            'secondary': f"supply {round(r['net_pct'],1)}% · {_fmt_eur(r['value_now'])}",
        }

    pressure_html = (
        '<div class="dash-col"><h3 class="dash-subhead" title="Supply drained but price has not moved — possible upward pressure">Coiling</h3>'
        + _rows([pressure_row(r, 'rgb(34,139,34)') for r in coiling[:TOP_N]]) + '</div>'
        '<div class="dash-col"><h3 class="dash-subhead" title="Price rising without supply shrinking">Overbought</h3>'
        + _rows([pressure_row(r, 'rgb(255,140,0)') for r in overbought[:TOP_N]]) + '</div>'
        '<div class="dash-col"><h3 class="dash-subhead" title="Price up but sellers are piling in">Cooling</h3>'
        + _rows([pressure_row(r, 'rgb(220,53,69)') for r in cooling[:TOP_N]]) + '</div>'
    )

    return {
        'movers_html': movers_html,
        'supply_html': supply_html,
        'pressure_html': pressure_html,
    }
