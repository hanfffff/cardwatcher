"""WP2 tests: one bad row or file never aborts import (app/watcherbase.py)."""
import os

from bs4 import BeautifulSoup

import app.watcherbase as wb
from app.page import Page
from app.watcherbase import watcherbase


# --- per-row isolation ------------------------------------------------------

def test_parse_listings_skips_malformed_row(listing_rows_html):
    good_row = BeautifulSoup(listing_rows_html, "html.parser").find(
        id="row-server-rendered")
    # A valid row plus an empty (malformed) one that will raise when parsed.
    table_html = (
        '<div class="table-body">'
        + str(good_row)
        + '<div id="badrow" class="article-row"></div>'
        + '</div>'
    )
    table_body = BeautifulSoup(table_html, "html.parser").find(
        "div", class_="table-body")

    page = Page()
    page.card = "Test Card"
    page.canonical_name = "Test_Card"
    report = {"rows_skipped": 0}

    prices = watcherbase._parse_listings(table_body, page, 1700000000.0, report)

    assert prices == [12.5]            # only the good row contributed a price
    assert len(page.listings) == 1     # malformed row not added
    assert report["rows_skipped"] == 1


def test_parse_listings_none_table_body():
    report = {"rows_skipped": 0}
    assert watcherbase._parse_listings(None, Page(), 0.0, report) == []
    assert report["rows_skipped"] == 0


# --- per-file isolation -----------------------------------------------------

def test_import_all_pages_quarantines_failing_file(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    failed = downloads / "failed"
    changes = tmp_path / "changes"
    downloads.mkdir()
    changes.mkdir()

    for name in ("good1.htm", "bad.htm", "good2.htm"):
        (downloads / name).write_text("<html></html>", encoding="utf-8")

    monkeypatch.setattr(wb, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(wb, "FAILED_DIR", str(failed))
    monkeypatch.setattr(wb, "CHANGES_DIR", str(changes))

    def fake_import_one(file_name, timestamp, price_history, report):
        if file_name == "bad.htm":
            raise ValueError("boom")
        return "imported"

    monkeypatch.setattr(watcherbase, "_import_one_file", fake_import_one)

    report = watcherbase.import_all_pages()

    assert report["imported"] == 2
    assert report["failed"] == ["bad.htm"]
    # bad file quarantined, not deleted; good files consumed by the (faked) import
    assert os.path.exists(failed / "bad.htm")
    assert not os.path.exists(downloads / "bad.htm")
    # the run still produced a price_history.json
    assert os.path.exists(changes / "price_history.json")


def test_import_all_pages_picks_up_html_and_htm(tmp_path, monkeypatch):
    """A browser "Save page as..." writes .html, our downloader writes .htm."""
    downloads = tmp_path / "downloads"
    changes = tmp_path / "changes"
    downloads.mkdir()
    changes.mkdir()

    for name in ("saved.html", "downloaded.htm", "notes.txt"):
        (downloads / name).write_text("<html></html>", encoding="utf-8")
    # A directory must never be treated as a page, whatever it is named.
    (downloads / "stale.htm").mkdir()

    monkeypatch.setattr(wb, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(wb, "CHANGES_DIR", str(changes))

    seen = []

    def fake_import_one(file_name, timestamp, price_history, report):
        seen.append(file_name)
        return "imported"

    monkeypatch.setattr(watcherbase, "_import_one_file", fake_import_one)

    report = watcherbase.import_all_pages()

    assert sorted(seen) == ["downloaded.htm", "saved.html"]
    assert report["imported"] == 2
    assert report["failed"] == []


def test_get_asset_folders_handles_both_extensions():
    assert watcherbase.get_page_stem("123.html") == "123"
    assert watcherbase.get_page_stem("1_Yamato.htm") == "1_Yamato"
    # Unknown extension is left alone rather than silently truncated.
    assert watcherbase.get_page_stem("notes.txt") == "notes.txt"
    assert watcherbase.get_asset_folders("123.html") == ["123-Dateien", "123_files"]


def test_move_to_failed_moves_english_assets_folder(tmp_path, monkeypatch):
    """English Chrome names the assets folder <stem>_files, not <stem>-Dateien."""
    downloads = tmp_path / "downloads"
    failed = downloads / "failed"
    downloads.mkdir()
    (downloads / "123.html").write_text("x", encoding="utf-8")
    assets = downloads / "123_files"
    assets.mkdir()
    (assets / "img.jpg").write_text("y", encoding="utf-8")

    monkeypatch.setattr(wb, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(wb, "FAILED_DIR", str(failed))

    watcherbase.move_to_failed("123.html")

    assert os.path.exists(failed / "123.html")
    assert not os.path.exists(downloads / "123.html")
    assert os.path.exists(failed / "123_files" / "img.jpg")


def test_delete_download_removes_html_and_assets(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    (downloads / "123.html").write_text("x", encoding="utf-8")
    (downloads / "123_files").mkdir()
    (downloads / "123_files" / "img.jpg").write_text("y", encoding="utf-8")

    monkeypatch.setattr(wb, "DOWNLOADS_DIR", str(downloads))

    watcherbase.delete_download("123.html")

    assert not os.path.exists(downloads / "123.html")
    assert not os.path.exists(downloads / "123_files")


def test_import_all_pages_no_downloads_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(wb, "DOWNLOADS_DIR", str(tmp_path / "missing"))
    report = watcherbase.import_all_pages()
    assert report == {"imported": 0, "skipped": 0, "failed": [], "rows_skipped": 0}


# --- move_to_failed ---------------------------------------------------------

def test_move_to_failed_moves_file_and_assets(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    failed = downloads / "failed"
    downloads.mkdir()
    (downloads / "page.htm").write_text("x", encoding="utf-8")
    assets = downloads / "page-Dateien"
    assets.mkdir()
    (assets / "img.jpg").write_text("y", encoding="utf-8")

    monkeypatch.setattr(wb, "DOWNLOADS_DIR", str(downloads))
    monkeypatch.setattr(wb, "FAILED_DIR", str(failed))

    watcherbase.move_to_failed("page.htm")

    assert os.path.exists(failed / "page.htm")
    assert not os.path.exists(downloads / "page.htm")
    assert os.path.exists(failed / "page-Dateien" / "img.jpg")
