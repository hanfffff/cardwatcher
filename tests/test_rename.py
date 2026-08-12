"""Tests for app/rename.py -- repointing a card at a new CardMarket URL."""
import json
import os

import pytest

import app.rename as rn
from app.rename import canonical_from_url, rename_page

MIHAWK_URL = ("https://www.cardmarket.com/en/OnePiece/Products/Singles/"
              "Special-Tournament-Promos-Japanese/Dracule-Mihawk-OP01-070-V1")
MIHAWK_NEW = ("OnePiece_Products_Singles_Special-Tournament-Promos-Japanese_"
              "Dracule-Mihawk-OP01-070-V1")
MIHAWK_OLD = ("OnePiece_Products_Singles_Special-Tournament-Promos-Japanese_"
              "Dracule-Mihawk-OP01-070")


# --- canonical_from_url ------------------------------------------------------

def test_canonical_from_full_url():
    assert canonical_from_url(MIHAWK_URL) == MIHAWK_NEW


def test_canonical_drops_language_segment():
    # The same product off the German site must land on one identity.
    de = MIHAWK_URL.replace("/en/", "/de/")
    assert canonical_from_url(de) == MIHAWK_NEW


def test_canonical_from_scheme_less_paste():
    assert canonical_from_url("www.cardmarket.com/en/Magic/Products/Singles/Foo/Bar") \
        == "Magic_Products_Singles_Foo_Bar"


def test_canonical_from_bare_name():
    assert canonical_from_url(MIHAWK_NEW) == MIHAWK_NEW


def test_canonical_strips_query_and_trailing_json():
    assert canonical_from_url(MIHAWK_URL + "?language=1&minCondition=2") == MIHAWK_NEW
    assert canonical_from_url(MIHAWK_NEW + ".json") == MIHAWK_NEW


def test_canonical_rejects_other_host():
    with pytest.raises(ValueError):
        canonical_from_url("https://www.tcgplayer.com/en/Foo/Bar")


def test_canonical_rejects_empty():
    with pytest.raises(ValueError):
        canonical_from_url("   ")


def test_canonical_rejects_hostless_url():
    with pytest.raises(ValueError):
        canonical_from_url("https://www.cardmarket.com/en/")


# --- rename_page -------------------------------------------------------------

@pytest.fixture
def data_dirs(tmp_path, monkeypatch):
    """A throwaway data directory wired into app.rename's path constants."""
    dirs = {}
    for name, const in (("pages", "PAGES_DIR"), ("archive", "ARCHIVE_DIR"),
                        ("images", "IMAGES_DIR"), ("changes", "CHANGES_DIR")):
        path = tmp_path / name
        path.mkdir()
        monkeypatch.setattr(rn, const, str(path))
        dirs[name] = path
    return dirs


def write_page(folder, canonical, **extra):
    data = {
        "version": "1.0",
        "card": "Dracule Mihawk (OP01-070)",
        "set": "Special Tournament Promos",
        "canonical_name": canonical,
        "image": "data/images/" + canonical + ".jpg",
        "languages": ["Japanese"],
        "only_germany": "False",
        "available": 8,
        "available_graded": 8,
        "sold": 1,
        "inserted": 1,
        "listings": [{"card": "Dracule Mihawk (OP01-070)",
                      "canonical_name": canonical, "price": 190.0}],
    }
    data.update(extra)
    path = folder / (canonical + ".json")
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_rename_moves_page_and_rewrites_names(data_dirs):
    old_path = write_page(data_dirs["pages"], MIHAWK_OLD)

    result = rename_page(MIHAWK_OLD + ".json", MIHAWK_URL)

    assert result["success"]
    assert result["new_page_name"] == MIHAWK_NEW + ".json"
    assert not old_path.exists()

    data = json.loads((data_dirs["pages"] / (MIHAWK_NEW + ".json")).read_text(encoding="utf-8"))
    assert data["canonical_name"] == MIHAWK_NEW
    assert data["listings"][0]["canonical_name"] == MIHAWK_NEW
    assert data["image"] == "data/images/" + MIHAWK_NEW + ".jpg"


def test_rename_preserves_fields_page_load_drops(data_dirs):
    # sold/inserted are not read back by Page.load_json, so a load/save round
    # trip would zero them -- the raw-JSON rewrite must keep them.
    write_page(data_dirs["pages"], MIHAWK_OLD)

    rename_page(MIHAWK_OLD, MIHAWK_URL)

    data = json.loads((data_dirs["pages"] / (MIHAWK_NEW + ".json")).read_text(encoding="utf-8"))
    assert data["sold"] == 1
    assert data["inserted"] == 1
    assert data["available_graded"] == 8


def test_rename_keeps_archived_page_in_archive(data_dirs):
    write_page(data_dirs["archive"], MIHAWK_OLD)

    assert rename_page(MIHAWK_OLD, MIHAWK_URL)["success"]

    assert (data_dirs["archive"] / (MIHAWK_NEW + ".json")).exists()
    assert not (data_dirs["pages"] / (MIHAWK_NEW + ".json")).exists()


def test_rename_moves_image(data_dirs):
    write_page(data_dirs["pages"], MIHAWK_OLD)
    (data_dirs["images"] / (MIHAWK_OLD + ".jpg")).write_bytes(b"jpeg")

    result = rename_page(MIHAWK_OLD, MIHAWK_URL)

    assert result["image_moved"]
    assert (data_dirs["images"] / (MIHAWK_NEW + ".jpg")).read_bytes() == b"jpeg"
    assert not (data_dirs["images"] / (MIHAWK_OLD + ".jpg")).exists()


def test_rename_moves_price_history_entry(data_dirs):
    write_page(data_dirs["pages"], MIHAWK_OLD)
    ph = data_dirs["changes"] / "price_history.json"
    ph.write_text(json.dumps({MIHAWK_OLD: {"current_min": 150.0}, "Other": {}}),
                  encoding="utf-8")

    result = rename_page(MIHAWK_OLD, MIHAWK_URL)

    assert result["history_moved"]
    history = json.loads(ph.read_text(encoding="utf-8"))
    assert MIHAWK_OLD not in history
    assert history[MIHAWK_NEW]["current_min"] == 150.0
    assert "Other" in history


def test_rename_updates_collection_items(data_dirs, tmp_path, monkeypatch):
    import app.collection as col

    collection_file = tmp_path / "collection.json"
    monkeypatch.setattr(col, "COLLECTION_FILE", str(collection_file))
    collection_file.write_text(json.dumps({"items": [
        {"canonical_name": MIHAWK_OLD, "condition": "NM", "quantity": 2},
        {"canonical_name": "Some_Other_Card", "condition": "NM", "quantity": 1},
    ]}), encoding="utf-8")
    write_page(data_dirs["pages"], MIHAWK_OLD)

    result = rename_page(MIHAWK_OLD, MIHAWK_URL)

    assert result["collection_items"] == 1
    items = json.loads(collection_file.read_text(encoding="utf-8"))["items"]
    names = sorted(i["canonical_name"] for i in items)
    assert names == sorted([MIHAWK_NEW, "Some_Other_Card"])


def test_rename_case_only_keeps_the_page(data_dirs):
    # On Windows the old and new file are the same file, so a naive
    # "write new, delete old" would delete the page it just wrote.
    write_page(data_dirs["pages"], "Test_Card_Foo")
    (data_dirs["images"] / "Test_Card_Foo.jpg").write_bytes(b"jpeg")

    result = rename_page("Test_Card_Foo", "https://www.cardmarket.com/en/Test/Card/foo")

    assert result["success"], result["message"]
    surviving = [f for f in os.listdir(data_dirs["pages"]) if f.lower() == "test_card_foo.json"]
    assert len(surviving) == 1
    data = json.loads((data_dirs["pages"] / surviving[0]).read_text(encoding="utf-8"))
    assert data["canonical_name"] == "Test_Card_foo"
    assert len(os.listdir(data_dirs["images"])) == 1


def test_rename_refuses_existing_target(data_dirs):
    write_page(data_dirs["pages"], MIHAWK_OLD)
    write_page(data_dirs["archive"], MIHAWK_NEW)

    result = rename_page(MIHAWK_OLD, MIHAWK_URL)

    assert not result["success"]
    assert MIHAWK_NEW in result["message"]
    # The original is untouched.
    assert (data_dirs["pages"] / (MIHAWK_OLD + ".json")).exists()


def test_rename_refuses_same_link(data_dirs):
    write_page(data_dirs["pages"], MIHAWK_OLD)
    result = rename_page(MIHAWK_OLD, MIHAWK_URL.replace("-V1", ""))
    assert not result["success"]


def test_rename_missing_page(data_dirs):
    result = rename_page(MIHAWK_OLD, MIHAWK_URL)
    assert not result["success"]
    assert "no saved page" in result["message"].lower()


def test_rename_reports_unreadable_page_file(data_dirs):
    # A hand-edited or half-written page file must come back as a message, not
    # as a 500 out of the route.
    (data_dirs["pages"] / (MIHAWK_OLD + ".json")).write_text("{ not json",
                                                             encoding="utf-8")

    result = rename_page(MIHAWK_OLD, MIHAWK_URL)

    assert not result["success"]
    assert "could not read" in result["message"].lower()
    assert (data_dirs["pages"] / (MIHAWK_OLD + ".json")).exists()


def test_rename_reports_bad_url(data_dirs):
    write_page(data_dirs["pages"], MIHAWK_OLD)
    result = rename_page(MIHAWK_OLD, "https://example.com/foo")
    assert not result["success"]
    assert (data_dirs["pages"] / (MIHAWK_OLD + ".json")).exists()
    assert not os.listdir(data_dirs["archive"])
