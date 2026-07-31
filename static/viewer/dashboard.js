/* Dashboard controller — client-side port of app/dashboard.py:build_dashboard.
 *
 * Ranks cards from the precomputed changes/price_history.json into three signal
 * panels (biggest movers, net supply loss, pressure/divergence), using the
 * market floor and the same thresholds as the Flask dashboard.
 */
(function () {
  "use strict";
  const { escapeHtml, fetchJSON } = window.CW;

  // The canonical "market value" every panel ranks and displays. The floor (low
  // band of current asks) rather than the blend: the blend carries a realized-
  // sales term, so one outlier sale — a graded/PSA copy going for a multiple of
  // the raw price — drags it up sharply while the actual ask floor has not moved
  // at all. That surfaced as phantom movers. The floor only moves when sellers
  // reprice, and it is also what the browse view shows, so the two now agree.
  const MARKET_VALUE = "floor";

  // Pull the canonical market value out of an entry or one of its period dicts.
  const marketValue = (c) => ((c || {}).market || {})[MARKET_VALUE] || 0;

  // Tunable thresholds (identical to dashboard.py).
  const MIN_AVAILABLE = 10, MIN_BASE = 10, TOP_N = 8;
  const SUPPLY_DROP = 15, PRICE_FLAT = 5, PRICE_UP = 8, SUPPLY_GROW = 10;

  const r1 = (v) => Math.round(v * 10) / 10;

  function cardMeta(canonical) {
    return {
      file: canonical + ".json",
      name: canonical.split("_").pop().replace(/-/g, " "),
      image: window.CW.imagePath(canonical),
    };
  }

  function tint(color, alpha) {
    alpha = alpha || "0.12";
    color = color.trim();
    if (color.startsWith("rgb(") && color.endsWith(")")) {
      return "rgba(" + color.slice(4, -1) + "," + alpha + ")";
    }
    return "rgba(0,0,0,0.05)";
  }

  function fmtEur(v) {
    if (!v) return "--€";
    return (v >= 1000 ? String(Math.trunc(v)) : String(Math.round(v * 100) / 100)) + "€";
  }

  // Age (in days) of each historical anchor, oldest first. `now` is age 0.
  const ANCHORS = [
    { key: "6m", age: 180 },
    { key: "2m", age: 60 },
    { key: "1m", age: 30 },
    { key: "1w", age: 7 },
  ];

  // Build a {age, value} trend series from an entry's period snapshots.
  //   kind "price"  -> market value at each anchor + now
  //   kind "supply" -> available-listing count at each anchor + now
  function seriesFrom(entry, kind) {
    const pts = [];
    for (const a of ANCHORS) {
      const period = entry[a.key];
      if (!period) continue;
      const v = kind === "price"
        ? marketValue(period)
        : period.historical_available;
      if (typeof v === "number" && v > 0) pts.push({ age: a.age, value: v });
    }
    const nowVal = kind === "price"
      ? marketValue(entry)
      : entry.current_available;
    if (typeof nowVal === "number" && nowVal > 0) pts.push({ age: 0, value: nowVal });
    return pts;
  }

  // Render a compact trend sparkline as inline SVG. `pts` is [{age, value}]
  // oldest→newest; x is time-proportional across the card's own history span,
  // y is normalized to the series' own min/max. Returns '' for <2 points.
  function sparkline(pts, color) {
    if (!pts || pts.length < 2) return "";
    const W = 104, H = 40, PADX = 2, PADY = 4;
    const iw = W - PADX * 2, ih = H - PADY * 2;
    const maxAge = pts[0].age || 1;            // oldest anchor -> left edge
    const values = pts.map((p) => p.value);
    const lo = Math.min(...values), hi = Math.max(...values);
    const span = hi - lo || 1;
    const xy = pts.map((p) => {
      const x = PADX + (1 - p.age / maxAge) * iw;
      const y = PADY + (1 - (p.value - lo) / span) * ih;
      return [Math.round(x * 10) / 10, Math.round(y * 10) / 10];
    });
    const line = xy.map((p, i) => (i ? "L" : "M") + p[0] + " " + p[1]).join(" ");
    const area = line + " L" + xy[xy.length - 1][0] + " " + (H - PADY) +
      " L" + xy[0][0] + " " + (H - PADY) + " Z";
    const last = xy[xy.length - 1];
    const gid = "sg" + (sparkline._n = (sparkline._n || 0) + 1);
    return (
      '<svg class="dash-spark" viewBox="0 0 ' + W + " " + H + '" ' +
        'preserveAspectRatio="none" aria-hidden="true" style="--spark:' + color + '">' +
        "<defs><linearGradient id=\"" + gid + "\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
          '<stop offset="0" stop-color="' + color + '" stop-opacity="0.22"/>' +
          '<stop offset="1" stop-color="' + color + '" stop-opacity="0"/>' +
        "</linearGradient></defs>" +
        '<path d="' + area + '" fill="url(#' + gid + ')"/>' +
        '<path d="' + line + '" fill="none" stroke="' + color + '" ' +
          'stroke-width="2" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>' +
        '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="2.6" fill="' + color + '"/>' +
      "</svg>"
    );
  }

  function rows(entries) {
    if (!entries.length) return '<div class="dash-empty">No cards meet the criteria yet.</div>';
    return entries.map((e) => {
      const meta = cardMeta(e.canonical);
      const color = e.color || "#444";
      const spark = sparkline(e.spark, e.sparkColor || color);
      return (
        '<a href="' + window.CW.cfg().cardHref(meta.file, false) + '" class="dash-card" style="--accent:' + color + ";--tint:" + tint(color) + ';">' +
          '<img src="' + meta.image + '" alt="" class="lazy dash-thumb">' +
          '<div class="dash-info">' +
            '<div class="dash-primary">' + e.primary + "</div>" +
            '<div class="dash-name">' + escapeHtml(meta.name) + "</div>" +
            '<div class="dash-secondary">' + e.secondary + "</div>" +
            (spark ? '<div class="dash-spark-wrap">' + spark + "</div>" : "") +
          "</div>" +
        "</a>"
      );
    }).join("");
  }

  function build(priceHistory, activePages) {
    const movers = [], supply = [], pressureRows = [];

    for (const canonical in priceHistory) {
      if (activePages.indexOf(canonical) === -1) continue;
      const entry = priceHistory[canonical];
      const valueNow = marketValue(entry);
      const available = entry.current_available || 0;
      const wk = entry["1w"] || {};
      const value1w = marketValue(wk);

      const priceSeries = seriesFrom(entry, "price");
      const supplySeries = seriesFrom(entry, "supply");

      let pricePct = null;
      if (valueNow > 0 && value1w > 0 && available >= MIN_AVAILABLE) {
        pricePct = (valueNow - value1w) / value1w * 100;
        movers.push({ pct: pricePct, canonical, now: valueNow, prev: value1w, priceSeries });
      }

      const base = wk.historical_available || 0;
      let netPct = null;
      if (base >= MIN_BASE) {
        // Net item change over the week — the same quantity the sparkline's last
        // segment plots (current_available vs the 1w anchor), so the % badge and
        // the graph can never disagree. Fall back to gross flow if unavailable.
        const netItems = (wk.available_change != null)
          ? wk.available_change
          : ((wk.listings_added || 0) - (wk.listings_removed || 0));
        netPct = netItems / base * 100;
        if (netPct < 0) supply.push({ netPct, canonical, base, curAvail: available, valueNow, supplySeries });
      }

      if (pricePct != null && netPct != null) {
        pressureRows.push({ canonical, price_pct: pricePct, net_pct: netPct, value_now: valueNow, priceSeries });
      }
    }

    // Movers: top risers and fallers side by side.
    movers.sort((a, b) => b.pct - a.pct);
    const risers = movers.slice(0, TOP_N);
    let fallers = movers.length > TOP_N ? movers.slice(-TOP_N).sort((a, b) => a.pct - b.pct) : [];
    const riserNames = new Set(risers.map((m) => m.canonical));
    fallers = fallers.filter((f) => !riserNames.has(f.canonical)).slice(0, TOP_N);

    const moverRow = (m) => {
      const sign = m.pct >= 0 ? "+" : "";
      const color = m.pct > 0 ? "rgb(34,139,34)" : (m.pct < 0 ? "rgb(220,53,69)" : "#666");
      const arrow = m.pct > 0 ? " ↑" : (m.pct < 0 ? " ↓" : "");
      return { canonical: m.canonical, color, primary: sign + r1(m.pct) + "%" + arrow, secondary: fmtEur(m.prev) + " → " + fmtEur(m.now), spark: m.priceSeries };
    };

    const moversHtml =
      '<div class="dash-col"><h3 class="dash-subhead">▲ Gainers</h3>' + rows(risers.map(moverRow)) + "</div>" +
      '<div class="dash-col"><h3 class="dash-subhead">▼ Fallers</h3>' + rows(fallers.map(moverRow)) + "</div>";

    // Net supply loss.
    supply.sort((a, b) => a.netPct - b.netPct);
    const supplyRow = (s) => ({
      canonical: s.canonical, color: "rgb(220,53,69)",
      primary: r1(s.netPct) + "% supply",
      secondary: s.base + "→" + s.curAvail + " avail · " + fmtEur(s.valueNow),
      spark: s.supplySeries,
    });
    const supplyHtml = rows(supply.slice(0, TOP_N).map(supplyRow));

    // Pressure / divergence buckets.
    const coiling = [], overbought = [], cooling = [];
    for (const r of pressureRows) {
      const pp = r.price_pct, np = r.net_pct;
      if (np <= -SUPPLY_DROP && Math.abs(pp) < PRICE_FLAT) coiling.push(r);
      else if (pp >= PRICE_UP && np >= SUPPLY_GROW) cooling.push(r);
      else if (pp >= PRICE_UP && np > -PRICE_FLAT) overbought.push(r);
    }
    coiling.sort((a, b) => a.net_pct - b.net_pct);
    overbought.sort((a, b) => b.price_pct - a.price_pct);
    cooling.sort((a, b) => b.price_pct - a.price_pct);

    const pressureRow = (r, color) => ({
      canonical: r.canonical, color,
      primary: "price " + (r.price_pct >= 0 ? "+" : "") + r1(r.price_pct) + "%",
      secondary: "supply " + r1(r.net_pct) + "% · " + fmtEur(r.value_now),
      spark: r.priceSeries, sparkColor: color,
    });

    const pressureHtml =
      '<div class="dash-col"><h3 class="dash-subhead" title="Supply drained but price has not moved — possible upward pressure">Coiling</h3>' +
        rows(coiling.slice(0, TOP_N).map((r) => pressureRow(r, "rgb(34,139,34)"))) + "</div>" +
      '<div class="dash-col"><h3 class="dash-subhead" title="Price rising without supply shrinking">Overbought</h3>' +
        rows(overbought.slice(0, TOP_N).map((r) => pressureRow(r, "rgb(255,140,0)"))) + "</div>" +
      '<div class="dash-col"><h3 class="dash-subhead" title="Price up but sellers are piling in">Cooling</h3>' +
        rows(cooling.slice(0, TOP_N).map((r) => pressureRow(r, "rgb(220,53,69)"))) + "</div>";

    return { moversHtml, supplyHtml, pressureHtml };
  }

  async function main() {
    let priceHistory, manifest;
    try {
      const c = CW.cfg();
      [priceHistory, manifest] = await Promise.all([
        fetchJSON(c.priceHistoryUrl),
        fetchJSON(c.manifestUrl),
      ]);
    } catch (e) {
      document.getElementById("moversPanel").innerHTML = '<div class="dash-empty">Failed to load data.</div>';
      console.error(e);
      return;
    }
    const activePages = (manifest.cards || []).map((c) => c.canonical);
    const out = build(priceHistory, activePages);
    document.getElementById("moversPanel").innerHTML = out.moversHtml;
    document.getElementById("supplyPanel").innerHTML = out.supplyHtml;
    document.getElementById("pressurePanel").innerHTML = out.pressureHtml;
  }

  document.addEventListener("DOMContentLoaded", main);
})();
