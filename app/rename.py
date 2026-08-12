"""Repoint a tracked card at a new CardMarket URL.

CardMarket moves products around now and then -- a set gets a new slug, a card
gets a "-V1" suffix -- and for us the URL *is* the identity: the canonical name
is the URL path with '/' turned into '_', and the page file, the card image, the
metrics in price_history.json and the collection entries are all keyed by it.
Downloading a moved product under its new URL would therefore start a second,
empty history next to the old one instead of continuing it. Renaming migrates
the whole key in one go, so the existing history simply carries on.

The page file is rewritten as raw JSON rather than loaded into a Page and saved
again on purpose: Page.load_json does not read `sold`/`inserted`, so a load/save
round trip would silently zero them.
"""
import json
import os
import shutil
from urllib.parse import urlparse

from app.config import PAGES_DIR, ARCHIVE_DIR, IMAGES_DIR, CHANGES_DIR

# Characters that must never reach a file name. The path segments come out of a
# URL so most of these cannot occur, but a hand-typed canonical name can carry
# anything.
_ILLEGAL = set('\\/:*?"<>|')


def canonical_from_url(url):
    """CardMarket product URL -> canonical name.

    Accepts a full URL, a scheme-less "www.cardmarket.com/en/..." paste, or an
    already-canonical name. The two-letter language segment ("en", "de", ...) is
    dropped, so the same product entered from the German site keeps one identity.

    Raises ValueError with a user-facing message if the input isn't usable.
    """
    text = (url or "").strip()
    if not text:
        raise ValueError("Please enter a CardMarket product URL.")

    lowered = text.lower()
    if "://" not in text and (lowered.startswith("www.") or lowered.startswith("cardmarket.com")):
        text = "https://" + text

    parsed = urlparse(text)
    if parsed.netloc:
        if "cardmarket.com" not in parsed.netloc.lower():
            raise ValueError("The link has to point to cardmarket.com.")
        path = parsed.path
    else:
        # No host: a pasted path or a bare canonical name. Query/fragment are
        # not stripped by urlparse in that case, so cut them off here.
        path = text.split("?")[0].split("#")[0]

    segments = [s for s in path.strip("/").split("/") if s]
    if segments and len(segments[0]) == 2 and segments[0].isalpha():
        segments = segments[1:]
    if not segments:
        raise ValueError("Could not read a product path out of that link.")

    canonical = "_".join(segments)
    if canonical.endswith(".json"):
        canonical = canonical[:-5]
    if not canonical or canonical.strip(". ") != canonical or _ILLEGAL & set(canonical):
        raise ValueError("That link contains characters that can't be used as a card name.")
    return canonical


def _page_location(canonical):
    """(directory, path) of an existing page file, or (None, None)."""
    for folder in (PAGES_DIR, ARCHIVE_DIR):
        path = os.path.join(folder, canonical + ".json")
        if os.path.exists(path):
            return folder, path
    return None, None


def _same_path(a, b):
    """Whether two paths are the same file for the filesystem (Windows: case)."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _rename_image(old, new):
    """Move images/<old>.jpg to images/<new>.jpg. Returns True if it now exists.

    An image already sitting under the new name wins (it was downloaded from the
    new URL and is at least as current); the old file is then left as an orphan
    rather than deleted, since deleting is the one step that cannot be undone.
    """
    new_path = os.path.join(IMAGES_DIR, new + ".jpg")
    old_path = os.path.join(IMAGES_DIR, old + ".jpg")
    if os.path.exists(new_path) and not _same_path(old_path, new_path):
        return True
    if not os.path.exists(old_path):
        return False
    try:
        # os.rename rather than shutil.move so a name that differs only in case
        # actually changes case on a case-insensitive filesystem.
        os.rename(old_path, new_path)
        return True
    except OSError as e:
        print("rename_page | could not move image: " + str(e))
        return False


def _rename_price_history(old, new):
    """Move the stored metrics to the new key. Returns True if there were any."""
    path = os.path.join(CHANGES_DIR, "price_history.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (json.JSONDecodeError, IOError):
        return False
    if old not in history:
        return False
    # The old entry is the history we are keeping, so it wins over anything that
    # happens to sit under the new key already.
    history[new] = history.pop(old)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    return True


def _rename_collection(old, new):
    """Repoint collection items at the new name. Returns how many moved."""
    from app.collection import Collection

    collection = Collection().load()
    moved = 0
    for item in collection.items:
        if item.canonical_name == old:
            item.canonical_name = new
            moved += 1
    if moved:
        collection.save()
    return moved


def rename_page(old_page_name, new_url):
    """Migrate a tracked card from its old canonical name to the new URL's.

    Moves the page file (staying in pages/ or archive/, whichever it was in),
    the card image, the price_history.json entry and any collection items.

    Returns a dict for jsonify: {success, message, new_page_name, ...}.
    """
    old = old_page_name[:-5] if old_page_name.endswith(".json") else old_page_name
    if not old:
        return {"success": False, "message": "No card given."}

    try:
        new = canonical_from_url(new_url)
    except ValueError as e:
        return {"success": False, "message": str(e)}

    if new == old:
        return {"success": False, "message": "That is already this card's link."}

    folder, old_path = _page_location(old)
    if not old_path:
        return {"success": False, "message": "This card has no saved page file."}

    new_path = os.path.join(folder, new + ".json")
    # A name differing only in case is the same file on Windows, and must not be
    # mistaken for a second card -- nor deleted as "the old one" further down.
    same_file = _same_path(old_path, new_path)

    # Refuse to merge into an existing card: two histories for what may or may
    # not be the same product is a judgement call for the user, not for us.
    existing = _page_location(new)[1]
    if existing and not same_file:
        return {"success": False,
                "message": "Another tracked card already uses that link ("
                           + new + "). Archive or delete it first."}

    try:
        with open(old_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError, UnicodeDecodeError) as e:
        return {"success": False,
                "message": "Could not read this card's page file: " + str(e)}

    data["canonical_name"] = new
    for listing in data.get("listings", []):
        if "canonical_name" in listing:
            listing["canonical_name"] = new

    image_moved = _rename_image(old, new)
    if image_moved or data.get("image", "").endswith(old + ".jpg"):
        data["image"] = "data/images/" + new + ".jpg"

    # Write the new file first, drop the old one only once that succeeded, so a
    # failure mid-way leaves the original page intact.
    try:
        if same_file:
            # Same file, new spelling: rename first (that is what actually
            # changes the case), then rewrite the contents.
            os.rename(old_path, new_path)
            with open(new_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        else:
            with open(new_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.remove(old_path)
    except OSError as e:
        return {"success": False, "message": "Could not write the page file: " + str(e)}

    history_moved = _rename_price_history(old, new)
    collection_items = _rename_collection(old, new)

    print("rename_page | " + old + " -> " + new)
    return {
        "success": True,
        "message": "Link updated.",
        "old_name": old,
        "new_name": new,
        "new_page_name": new + ".json",
        "image_moved": image_moved,
        "history_moved": history_moved,
        "collection_items": collection_items,
    }
