import os
import json
from datetime import datetime
import time
import math
from app.listing import Listing, Seller
from app.language_libraries import *
from app.grading_libraries import (COMPANY_LABELS, COMPANY_ORDER,
                                   format_grade_number, grade_slug)
from app.config import PAGES_DIR, ARCHIVE_DIR


def _grade_compatible(old_listing, new_listing):
    """Whether two listings' grades allow them to be the same tracked listing.

    Grade belongs in the match key because without it a seller offering a PSA 10
    and a PSA 9 of one card collapses into a single tracked listing whose price
    history flips between two unrelated price levels.

    Two cases deliberately match anything:
    - A manually set grade. The user's correction is authoritative precisely
      because the comment could not be trusted, so the comment must not decide
      identity either.
    - grade_company None, i.e. a listing saved before grading support. Same
      wildcard first_ed == 2 gets, so existing history is not torn up on the
      first import after this feature lands.
    """
    if old_listing.grade_source == 'manual':
        return True
    if old_listing.grade_company is None:
        return True
    return (old_listing.grade_company == new_listing.grade_company
            and old_listing.grade == new_listing.grade)


class Page:
        
    def __init__(self):
        # these variables stay the same
        self.card = ""
        self.set = ""
        self.canonical_name = ""
        self.image = ""
        self.sold = 0
        self.inserted = 0

        # these are important when updating the page
        self.listings = []
        self.languages = []
        self.only_germany = False
        # available counts RAW copies only -- a slab is not supply of the card a
        # buyer of the raw card is shopping for. Graded supply is tracked next to
        # it so nothing is lost.
        self.available = 0
        self.available_graded = 0

        # these are for plotting the information
        self.xdata = []
        self.ydata = []

        self.loadMoreButton = False

        self.isArchived = False
            
    def __str__(self):
        output = self.card
        output += "," + self.set +",["
        for language in self.languages:
            output += language + ";"
        if output[-1] == ";":
            output = output[0:-1]
        output += "]"
        output += "," + str(self.only_germany)
        output += "," + self.image
        output += "," + str(self.available)
        for listing in self.listings:
            output += "\n" + str(listing)
        return output

    def save(self):
        # Save in JSON format only
        self.save_json()

    def save_json(self):
        """Save page in JSON format."""
        folder = ARCHIVE_DIR if self.isArchived else PAGES_DIR
        filepath = os.path.join(folder, self.canonical_name + ".json")

        page_data = {
            'version': '1.0',
            'card': self.card,
            'set': self.set,
            'canonical_name': self.canonical_name,
            'image': self.image,
            'languages': self.languages,
            'only_germany': self.only_germany,
            'available': self.available,
            'available_graded': self.available_graded,
            'sold': self.sold,
            'inserted': self.inserted,
            'listings': []
        }

        # Convert all listings to JSON
        for listing in self.listings:
            listing_data = listing.to_json()
            page_data['listings'].append(listing_data)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(page_data, f, indent=2, ensure_ascii=False)

    def load_json(self, file):
        """Load page from JSON format."""
        self.isArchived = False
        filepath = None

        # Try pages directory first
        pages_path = os.path.join(PAGES_DIR, os.path.basename(file))
        if os.path.exists(pages_path):
            filepath = pages_path
        else:
            # Try archive directory
            archive_path = os.path.join(ARCHIVE_DIR, os.path.basename(file))
            if os.path.exists(archive_path):
                filepath = archive_path
                self.isArchived = True

        if not filepath:
            print("No JSON page found.")
            return False

        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)

        # Load page metadata
        self.card = data.get('card', '')
        self.set = data.get('set', '')
        self.canonical_name = data.get('canonical_name', '')
        image = data.get('image', '')
        # Remap old image path format to new
        if image.startswith("static/Blanko/images/"):
            image = "data/images/" + image[len("static/Blanko/images/"):]
        self.image = image
        self.languages = data.get('languages', [])
        self.only_germany = data.get('only_germany', False)
        self.available = data.get('available', 0)
        self.available_graded = data.get('available_graded', 0)

        # Load listings
        self.listings = []
        prices = []

        for listing_data in data.get('listings', []):
            listing = Listing()
            listing.from_json(listing_data)
            listing.canonical_name = self.canonical_name
            self.listings.append(listing)

            # Graded copies are left out of the page average: a 800 EUR PSA 10
            # and a 40 EUR raw NM are not two samples of one price.
            if not listing.ended and not listing.is_graded():
                prices.append(listing.price)

        self.price_average = Page.calculate_price_average_robust(prices)

        return True

    def import_page(self,file):
        # Try JSON format first (preferred)
        json_file = os.path.basename(file) + '.json' if not file.endswith('.json') else os.path.basename(file)
        json_pages_path = os.path.join(PAGES_DIR, json_file)
        json_archive_path = os.path.join(ARCHIVE_DIR, json_file)

        if os.path.exists(json_pages_path) or os.path.exists(json_archive_path):
            # Use JSON format
            if self.load_json(json_file):
                return
            else:
                print("Failed to load JSON format, falling back to old format")

        # Fall back to old format
        lines = ""
        first = True
        self.isArchived = False
        try:
            with open(os.path.join(PAGES_DIR,os.path.basename(file)),'r',encoding='utf-8') as f:
                lines = f.read()
        except FileNotFoundError:
            try:
                with open(os.path.join(ARCHIVE_DIR,os.path.basename(file)),'r',encoding='utf-8') as f:
                    lines = f.read()
                    self.isArchived = True
            except FileNotFoundError:
                print("No previous page found.")
                return

        prices = []
        for line in lines.split('\n'):
            if first:
                card_,set_,languages_,only_germany_,image_,available_ = line.split(',')
                self.card = card_
                self.set = set_
                self.languages = languages_[1:-1].split(';')
                self.only_germany = True if only_germany_ == "True" else "False"
                self.image = image_
                self.available = int(available_)
                first = False
                continue
            listing = Listing()
            listing.import_listing(line)
            listing.canonical_name = self.canonical_name
            self.listings.append(listing)
            if not listing.ended and not listing.is_graded():
                prices.append(listing.price)
        from watcherbase import watcherbase
        self.price_average = self.calculate_price_average_robust(prices)

    def update_page(self,page):
        if self.canonical_name != page.canonical_name:
            print("ERROR: wrong page. Can't update!")
            return
        new_listings = []
        self.available = 0
        self.card = page.card
        self.set = page.set
        self.canonical_name = page.canonical_name
        self.image = page.image
        self.sold = 0
        self.inserted = 0

        highest_price = page.listings[-1].price if len(page.listings) > 0 else 0

        # find all the listings that were previously on the page
        while len(page.listings) > 0:
            new_listing = Listing()
            new_listing.card = page.listings[0].card
            new_listing.seller = Seller()
            new_listing.seller.name = page.listings[0].seller.name
            new_listing.seller.country = page.listings[0].seller.country
            new_listing.price = page.listings[0].price
            new_listing.date = page.listings[0].date
            new_listing.language = page.listings[0].language
            new_listing.condition = page.listings[0].condition
            new_listing.quantity = page.listings[0].quantity
            # currently, the first seen date is this one
            new_listing.first_date = page.listings[0].date
            # the last seen date is this one
            new_listing.comment = page.listings[0].comment
            new_listing.first_ed = page.listings[0].first_ed
            new_listing.reverse_holo = page.listings[0].reverse_holo
            new_listing.grade_company = page.listings[0].grade_company
            new_listing.grade = page.listings[0].grade
            new_listing.grade_source = page.listings[0].grade_source
            new_listing.canonical_name = self.canonical_name
            self.inserted += 1

            # check if the listing was there before
            for listing in self.listings:
                if (    listing.seller.name == page.listings[0].seller.name
                        and listing.language == page.listings[0].language
                        and listing.condition == page.listings[0].condition
                        and (listing.first_ed == page.listings[0].first_ed or listing.first_ed == 2)
                        and (listing.reverse_holo == page.listings[0].reverse_holo or listing.reverse_holo == 2)
                        and _grade_compatible(listing, page.listings[0])):
                    # we have found an older first date and the listing is not new
                    new_listing.first_date = listing.first_date
                    new_listing.new = False

                    # check if the price has changed
                    new_listing.previous_prices = listing.previous_prices
                    if (listing.price != page.listings[0].price):
                        new_listing.price_is_new = True
                        new_listing.previous_prices.append((listing.price,listing.date))

                    # check if the quantity has changed
                    new_listing.previous_quantities = listing.previous_quantities[:]
                    new_listing.quantity_change = page.listings[0].quantity - listing.quantity
                    if new_listing.quantity_change != 0:
                        new_listing.previous_quantities.append((listing.quantity, listing.date))

                    # preserve archived status
                    new_listing.archived = listing.archived

                    # A grade the user set by hand outranks whatever this
                    # import parsed out of the comment -- without carrying it
                    # over, every import would silently undo the correction.
                    if listing.grade_source == 'manual':
                        new_listing.grade_company = listing.grade_company
                        new_listing.grade = listing.grade
                        new_listing.grade_source = 'manual'

                    # check if the listing had ended before
                    if listing.ended:
                        new_listing.comment = "RELISTED! " + page.listings[0].comment
                    # if not, it was there before and not actually inserted
                    else:
                        self.inserted -= 1

                    # since we already updated the listing we can remove it from the previous listings
                    self.listings.remove(listing)
                    break
                    
            # remove the old listing and save the new one
            page.listings.pop(0)
            new_listings.append(new_listing)

        # work through all the listings that are no longer available
        while len(self.listings) > 0:
            new_listing = Listing()
            new_listing.card = self.listings[0].card
            new_listing.seller = Seller()
            new_listing.seller.name = self.listings[0].seller.name
            new_listing.seller.country = self.listings[0].seller.country
            new_listing.price = self.listings[0].price
            new_listing.language = self.listings[0].language
            new_listing.condition = self.listings[0].condition
            new_listing.previous_prices = self.listings[0].previous_prices
            new_listing.previous_quantities = self.listings[0].previous_quantities[:]
            new_listing.quantity = self.listings[0].quantity
            new_listing.first_date = self.listings[0].first_date
            new_listing.comment = self.listings[0].comment
            new_listing.new = False
            new_listing.first_ed = self.listings[0].first_ed
            new_listing.reverse_holo = self.listings[0].reverse_holo
            new_listing.grade_company = self.listings[0].grade_company
            new_listing.grade = self.listings[0].grade
            new_listing.grade_source = self.listings[0].grade_source
            new_listing.canonical_name = self.canonical_name
            # preserve archived status
            new_listing.archived = self.listings[0].archived
            # if the new page only has listings from germany, we don't want to show listings from other countries as ended
            if (page.only_germany
                and location_to_english.get(new_listing.seller.country, new_listing.seller.country) !=  "Item location: Germany"):
                new_listing.ended = self.listings[0].ended
            # otherwise, if the language of the old listing is not in the new set of chosen languages, don't show it as ended, either
            elif language_to_english.get(new_listing.language, new_listing.language) not in page.languages:
                new_listing.ended = self.listings[0].ended
            # if the user did not load all listings, don't let listings end that have a higher price than the last listing.
            elif page.loadMoreButton and self.listings[0].price >= highest_price:
                new_listing.ended = self.listings[0].ended
            else:
                new_listing.ended = True
                new_listing.new = not self.listings[0].ended
                if new_listing.new:
                    self.sold += 1
                if not self.listings[0].ended and self.listings[0].quantity > 0:
                    new_listing.previous_quantities.append((self.listings[0].quantity, self.listings[0].date))
                new_listing.quantity = 0
            new_listing.date = self.listings[0].date
            # the last seen date is the date of the previous listing
            new_listing.quantity_change = new_listing.quantity - self.listings[0].quantity

            # remove the old listing and save the new one
            self.listings.pop(0)
            new_listings.append(new_listing)

        # save the new listings in sorted order
        self.listings = sorted(new_listings, key = lambda x: x.price)

        # Recompute available as true quantity sum, excluding archived and ended
        # (can't do this earlier because archived status isn't known until after matching)
        live = [l for l in self.listings if not l.ended and not l.archived]
        self.available = sum(l.quantity for l in live if not l.is_graded())
        self.available_graded = sum(l.quantity for l in live if l.is_graded())

        for language in page.languages:
            if language not in self.languages:
                self.languages.append(language)

        if "price_average" in self.__dict__:
            self.price_change = page.price_average - self.price_average
        else:
            self.price_change = 0
            
        self.price_average = page.price_average

    def delete_listings(self,delete_list):
        print("delete_listings | delete: " + str(delete_list))
        new_listings = []
        self.available = 0
        for i in range(len(self.listings)):
            if i not in delete_list:
                self.available += self.listings[i].quantity
                new_listings.append(self.listings[i])
        self.listings = new_listings
        self.save()

    def archive_listing(self, index):
        """Archive a listing (exclude from calculations but keep visible)."""
        if 0 <= index < len(self.listings):
            self.listings[index].archived = True
            print(f"archive_listing | archived listing {index}")
            self.save()
            return True
        return False

    def unarchive_listing(self, index):
        """Unarchive a listing (include in calculations again)."""
        if 0 <= index < len(self.listings):
            self.listings[index].archived = False
            print(f"unarchive_listing | unarchived listing {index}")
            self.save()
            return True
        return False

    def set_listing_grade(self, index, company, grade):
        """Set a listing's grade by hand and mark it as user-owned.

        ``company=""`` clears the grade, which is how a false positive gets
        killed (a "Don't expect PSA10" that slipped through, say). The 'manual'
        source is what makes the change survive the next import.
        """
        if not (0 <= index < len(self.listings)):
            return False
        listing = self.listings[index]
        listing.grade_company = company or ""
        listing.grade = float(grade) if (company and grade is not None) else None
        listing.grade_source = 'manual'
        print(f"set_listing_grade | listing {index} -> {listing.grade_label() or 'not graded'}")
        self.save()
        return True

    def build_grading_selection(self):
        """Filter checkboxes for the grades actually present on this page.

        Two groups: one checkbox per grading company (plus "Not graded"), and one
        per distinct grade number. Mirrors build_country_selection's markup so the
        existing filter styling applies unchanged.
        """
        companies = []
        grades = []
        has_raw = False
        for listing in self.listings:
            if listing.is_graded():
                if listing.grade_company not in companies:
                    companies.append(listing.grade_company)
                if listing.grade is not None and listing.grade not in grades:
                    grades.append(listing.grade)
            else:
                has_raw = True

        if not companies:
            return ""

        companies.sort(key=lambda c: COMPANY_ORDER.index(c) if c in COMPANY_ORDER else 99)
        grades.sort(reverse=True)

        selection = ""
        for company in companies:
            value = "grade-" + company.lower()
            label = COMPANY_LABELS.get(company, company)
            selection += """<div class="form-check">
                                <input type="checkbox" id=\"""" + value + """" value=\"""" + value + """" class="grade-checkbox form-check-input mb-1 me-2">
                                <label for=\"""" + value + """" class="d-inline-flex form-check-label">
                                    <span>""" + label + """</span>
                                </label>
                            </div>"""
        if has_raw:
            selection += """<div class="form-check">
                                <input type="checkbox" id="grade-none" value="grade-none" class="grade-checkbox form-check-input mb-1 me-2">
                                <label for="grade-none" class="d-inline-flex form-check-label">
                                    <span>Not graded</span>
                                </label>
                            </div>"""
        selection += """<hr class="my-2">"""
        for grade in grades:
            value = "gradeval-" + grade_slug(grade)
            selection += """<div class="form-check">
                                <input type="checkbox" id=\"""" + value + """" value=\"""" + value + """" class="gradeval-checkbox form-check-input mb-1 me-2">
                                <label for=\"""" + value + """" class="d-inline-flex form-check-label">
                                    <span>Grade """ + format_grade_number(grade) + """</span>
                                </label>
                            </div>"""
        return selection

    def build_table(self):
        table = ""
        listing_counter = 0
        for listing in self.listings:
            listing.row_number = listing_counter
            table += listing.build_row()
            listing_counter += 1

        return table

    def build_country_selection(self):
        selection = ""
        countries = ["Item location: Germany"]
        for listing in self.listings:
            if listing.seller.country not in countries:
                countries.append(listing.seller.country)
        for country in countries:
            selection += """<div class="form-check">
	                            <input type="checkbox" name="sellerCountry[7]" id="sellerCountry-"""+country[15:]+"""" value=\"show-"""+country[15:]+"""" class="country-checkbox form-check-input mb-1 me-2">
                                <label for="sellerCountry-"""+country[15:]+"""" class="d-inline-flex form-check-label">
                                    <span style="display: inline-block; width: 16px; height: 16px; background-image:url('static/Blanko/ssMain.png'); background-position:"""+flags[country]+""";" class="icon align-self-center me-2">
                                    </span>
                                    <span>"""+country[15:]+"""</span>
                                </label>
                            </div>"""
        return selection

    def build_language_selection(self):
        selection = ""
        languages = []
        for listing in self.listings:
            if listing.language not in languages:
                languages.append(listing.language)
        for language in languages:
            selection += """<div class="form-check">
	                            <input type="checkbox" name="sellerCountry[7]" id="language-"""+language+"""" value=\"language-"""+language+"""" class="language-checkbox form-check-input mb-1 me-2">
                                <label for="language-"""+language+"""" class="d-inline-flex form-check-label">
                                    <span style="display: inline-block; width: 16px; height: 16px; background-image:url('static/Blanko/ssMain2.png'); background-position:"""+language_flags.get(language_to_english.get(language, language), "")+""";" class="icon align-self-center me-2">
                                    </span>
                                    <span>"""+language+"""</span>
                                </label>
                            </div>"""
        return selection
    
    def calculate_price_average_robust(prices):
        """
        Calculate price average with IQR-based outlier filtering.
        Falls back to simple mean for small datasets.

        Uses the Interquartile Range (IQR) method to detect and filter outliers:
        - Q1 = 25th percentile
        - Q3 = 75th percentile
        - IQR = Q3 - Q1
        - Lower bound = Q1 - 1.5 * IQR
        - Upper bound = Q3 + 1.5 * IQR
        - Values outside these bounds are considered outliers and excluded

        Args:
            prices: List of prices (floats)

        Returns:
            float: Filtered average price
        """
        if not prices:
            return 0.0

        if len(prices) < 4:
            # Too few data points for IQR, use simple mean
            return sum(prices) / len(prices)

        # Sort prices for quartile calculation
        sorted_prices = sorted(prices)
        n = len(sorted_prices)

        # Calculate quartiles (using simple method)
        q1_idx = n // 4
        q3_idx = 3 * n // 4
        q1 = sorted_prices[q1_idx]
        q3 = sorted_prices[q3_idx]

        # Calculate IQR and bounds
        iqr = q3 - q1

        # If IQR is 0 (all prices very similar), no filtering needed
        if iqr == 0:
            return sum(prices) / len(prices)

        lower_bound = q1 - 1.5 * iqr
        upper_bound = q3 + 1.5 * iqr

        # Filter outliers
        filtered_prices = [p for p in prices if lower_bound <= p <= upper_bound]

        # Ensure we don't filter out everything
        if not filtered_prices:
            # All were outliers? Use original data
            return sum(prices) / len(prices)

        return sum(filtered_prices) / len(filtered_prices)


    def build_graphs():
        return
            