"""Tests for app/dashboard.py — pressure bucketing and movers ranking.

build_dashboard reads a price_history.json and the pages directory; we point
both at a tmp dir and feed a synthetic price_history dict.
"""
import json
import os

import app.dashboard as dashboard


def _entry(value_now, value_1w, available, added, removed, base):
    # 'floor' spelled out rather than dashboard.MARKET_VALUE: the dashboard
    # ranking on the ask floor (not the sale-sensitive blend) is the behaviour
    # under test, so a switch back should fail here.
    return {
        "market": {"floor": value_now},
        "current_available": available,
        "1w": {
            "market": {"floor": value_1w},
            "listings_added": added,
            "listings_removed": removed,
            "historical_available": base,
        },
    }


def _setup(tmp_path, price_history, page_names):
    pages = tmp_path / "pages"
    changes = tmp_path / "changes"
    pages.mkdir()
    changes.mkdir()
    for name in page_names:
        (pages / (name + ".json")).write_text("{}", encoding="utf-8")
    (changes / "price_history.json").write_text(
        json.dumps(price_history), encoding="utf-8")
    return str(pages), str(changes)


def _segments(pressure_html):
    """Split pressure_html into its (coiling, overbought, cooling) segments."""
    ci = pressure_html.index("Coiling")
    oi = pressure_html.index("Overbought")
    li = pressure_html.index("Cooling")
    return pressure_html[ci:oi], pressure_html[oi:li], pressure_html[li:]


def test_pressure_bucketing(tmp_path, monkeypatch):
    price_history = {
        # supply drained hard (-50%), price flat (0%) -> coiling
        "coil": _entry(value_now=100, value_1w=100, available=20,
                       added=0, removed=10, base=20),
        # price up 20%, supply growing (+50%) -> cooling
        "cool": _entry(value_now=120, value_1w=100, available=20,
                       added=10, removed=0, base=20),
        # price up 20%, supply flat (0%) -> overbought
        "over": _entry(value_now=120, value_1w=100, available=20,
                       added=2, removed=2, base=20),
        # too few available -> excluded from movers/pressure entirely
        "thin": _entry(value_now=120, value_1w=100, available=5,
                       added=0, removed=0, base=20),
        # has metrics but no active page file -> excluded
        "gone": _entry(value_now=120, value_1w=100, available=20,
                       added=0, removed=10, base=20),
    }
    pages_dir, changes_dir = _setup(
        tmp_path, price_history, ["coil", "cool", "over", "thin"])
    monkeypatch.setattr(dashboard, "PAGES_DIR", pages_dir)
    monkeypatch.setattr(dashboard, "CHANGES_DIR", changes_dir)

    result = dashboard.build_dashboard()
    coiling, overbought, cooling = _segments(result["pressure_html"])

    assert "?name=coil.json" in coiling
    assert "?name=over.json" in overbought
    assert "?name=cool.json" in cooling

    # gone has no page file -> nowhere in the output
    assert "?name=gone.json" not in result["pressure_html"]
    assert "?name=gone.json" not in result["movers_html"]


def test_movers_ranking(tmp_path, monkeypatch):
    price_history = {
        "big": _entry(value_now=150, value_1w=100, available=20,   # +50%
                      added=0, removed=0, base=20),
        "small": _entry(value_now=110, value_1w=100, available=20,  # +10%
                        added=0, removed=0, base=20),
        "drop": _entry(value_now=80, value_1w=100, available=20,    # -20%
                       added=0, removed=0, base=20),
        "thin": _entry(value_now=200, value_1w=100, available=5,    # excluded
                       added=0, removed=0, base=20),
    }
    pages_dir, changes_dir = _setup(
        tmp_path, price_history, ["big", "small", "drop", "thin"])
    monkeypatch.setattr(dashboard, "PAGES_DIR", pages_dir)
    monkeypatch.setattr(dashboard, "CHANGES_DIR", changes_dir)

    movers_html = dashboard.build_dashboard()["movers_html"]

    # gainers show the biggest riser with its percentage; faller shows the drop
    assert "?name=big.json" in movers_html
    assert "+50.0%" in movers_html
    assert "?name=drop.json" in movers_html
    assert "-20.0%" in movers_html
    # low-availability card filtered out
    assert "?name=thin.json" not in movers_html


def test_build_dashboard_no_price_history(tmp_path, monkeypatch):
    pages = tmp_path / "pages"
    changes = tmp_path / "changes"
    pages.mkdir()
    changes.mkdir()
    monkeypatch.setattr(dashboard, "PAGES_DIR", str(pages))
    monkeypatch.setattr(dashboard, "CHANGES_DIR", str(changes))

    result = dashboard.build_dashboard()
    assert set(result) == {"movers_html", "supply_html", "pressure_html"}
