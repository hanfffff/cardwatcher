from datetime import datetime
from app.language_libraries import *
from app.grading_libraries import COMPANY_COLORS, format_grade, grade_slug, parse_grade
import math
import time
import json


def tooltip_label(tag):
    """Return the tooltip text of a CardMarket icon tag.

    CardMarket renders the location/language label in the `title` attribute
    server-side; Bootstrap's tooltip JS later moves it into `aria-label` (and
    `data-bs-original-title`). Pages captured before that JS runs only have
    `title`, so we try all the variants. Returns None if the tag is missing.
    """
    if tag is None:
        return None
    for attr in ('aria-label', 'title', 'data-bs-original-title', 'data-original-title'):
        val = tag.get(attr)
        if val:
            return val
    return None


class Seller:

    def __init__(self):
        self.name = ""
        self.country = ""

class Listing:

    def __init__(self):
        # the name of the card
        self.card = ""
        # the canonical name of the card
        self.canonical_name = ""
        # information about the seller
        self.seller = Seller()
        # the language of the card
        self.language = ""
        # the current price of the card
        self.price = 0.0
        # the current quantity of the card
        self.quantity = 0
        # the condition of the card
        self.condition = ""
        # comment by the seller
        self.comment = ""
        # the date of the current price
        self.date = ""
        # whether this listing has ended
        self.ended = False
        # whether this listing was seen before (when new and ended are set, the article is "newly ended")
        self.new = True
        # a list of tuples of the form (price,date)
        self.previous_prices = []
        # a list of tuples of the form (quantity,date)
        self.previous_quantities = []
        # the first date this card was listed by this seller
        self.first_date = ""
        # the last date this card was seen
        self.last_date = ""
        # whether the price has changed
        self.price_is_new = False
        # how much the quantity has changed
        self.quantity_change = 0
        # whether the card is first edition (0 = no, 1 = yes, 2 = unknown)
        self.first_ed = 2
        # whether the card is a reverse holo (0 = no, 1 = yes, 2 = unknown)
        self.reverse_holo = 2
        # whether this listing is archived (excluded from calculations but still visible)
        self.archived = False
        # the grading company, read out of the comment. Tri-state like first_ed:
        # None = never looked at (page predates grading support), "" = not
        # graded, "PSA"/"BGS"/... = graded by that company.
        self.grade_company = None
        # the numeric grade (10.0, 9.5, ...) or None when not graded
        self.grade = None
        # where the grade came from: None, 'auto' (parsed) or 'manual' (the user
        # corrected it, so re-imports must not overwrite it)
        self.grade_source = None

    def is_graded(self):
        """True when this listing is a professionally graded slab.

        Unknown (None) counts as not graded: a page saved before grading support
        has never been looked at, and treating it as graded would pull raw cards
        out of the raw price pool.
        """
        return bool(self.grade_company)

    def grade_label(self):
        """``PSA 10`` / ``BGS 9.5``, or '' when not graded."""
        return format_grade(self.grade_company, self.grade)

    def apply_parsed_grade(self):
        """Read the grade out of this listing's comment.

        Leaves a manually set grade alone -- the user's correction outranks
        whatever the seller wrote. Returns True when a review-worthy comment was
        seen (see grading_libraries.parse_grade).
        """
        if self.grade_source == 'manual':
            return False
        company, grade, needs_review = parse_grade(self.comment)
        self.grade_company = company
        self.grade = grade
        self.grade_source = 'auto'
        return needs_review

    def __str__(self):
        output = ("{" + \
                    self.card + \
                    ",(" + \
                        self.seller.name + ";" + \
                        self.seller.country +\
                    ")," + \
                    self.language + "," + \
                    self.condition + "," + \
                    str(self.price) + "," + \
                    str(self.quantity) + "," +\
                    self.comment.replace(',',';') + "," +\
                    str(self.date) + "," + \
                    str(self.ended) + "," +\
                    str(self.new) + "," +\
                    str([str(prev_price).replace(',','_') for prev_price in self.previous_prices]).replace(',',';') + "," +\
                    str(self.first_date) + "," +\
                    str(self.last_date) + "," +\
                    str(self.price_is_new) + "," +\
                    str(self.quantity_change) + ","+\
                    str(self.first_ed)+","+\
                    str(self.reverse_holo)+\
                  "}")
        return  output

    def import_listing(self,line):
        args_list = line[1:-1].split(',')
        if len(args_list) == 15:
            card_,seller_,language_,condition_,price_,quantity_,comment_,date_,ended_,new_,previous_prices_,first_date_,last_date_,price_is_new_,quantity_change_ = args_list
            first_ed_ = 2
            reverse_holo_ = 2
        elif len(args_list) == 16:
            card_,seller_,language_,condition_,price_,quantity_,comment_,date_,ended_,new_,previous_prices_,first_date_,last_date_,price_is_new_,quantity_change_,first_ed_ = args_list
            reverse_holo_ = 2
        elif len(args_list) == 17:
            card_,seller_,language_,condition_,price_,quantity_,comment_,date_,ended_,new_,previous_prices_,first_date_,last_date_,price_is_new_,quantity_change_,first_ed_,reverse_holo_ = args_list
        self.card = card_
        self.seller = Seller()
        self.seller.name = seller_[1:-1].split(';')[0]
        self.seller.country = seller_[1:-1].split(';')[1]
        self.language = language_
        self.condition = condition_
        self.price = float(price_)
        self.quantity = int(quantity_)
        self.comment = comment_
        self.date = date_
        self.ended = True if ended_ == "True" else False
        self.new = True if new_ == "True" else False
        # previous_prices of the form:  ["(1.0_ '17364656.3665')"; "(2.0_ '17575938.3665')"] or ['']
        self.previous_prices = []
        for prev_price in previous_prices_[1:-1].replace('\'','').split(";"):
            if prev_price == "":
                continue
            prev_price = prev_price.replace(')','').replace('(','').replace(' ','').replace("'",'').replace('"','').replace('[','').replace(']','')
            prev_price = prev_price.split('_')
            self.previous_prices.append(prev_price)
            # [(prev_price.replace(' ','')[2:-2].split('_')[0],prev_price.replace(' ','')[2:-2].split('_')[1][1:-1]) for prev_price in previous_prices_[1:-1].replace('\'','').split(";") if prev_price != ""]
        self.first_date = first_date_ if first_date_ else date_
        self.last_date = last_date_
        self.price_is_new = True if price_is_new_ == "True" else False
        self.quantity_change = int(quantity_change_)
        self.first_ed = int(first_ed_)
        self.reverse_holo = int(reverse_holo_)

    def to_json(self):
        """Convert listing to JSON-serializable dictionary."""
        # Ensure dates are stored as floats for consistency
        date_float = float(self.date) if self.date else 0.0
        first_date_float = float(self.first_date) if self.first_date else 0.0
        last_date_float = float(self.last_date) if self.last_date else 0.0
        # Convert previous_prices tuples to lists with float values
        prev_prices_normalized = [[float(p[0]), float(p[1])] for p in self.previous_prices if len(p) >= 2]
        prev_qtys_normalized = [[int(q[0]), float(q[1])] for q in self.previous_quantities if len(q) >= 2]
        return {
            'card': self.card,
            'canonical_name': self.canonical_name,
            'seller': {
                'name': self.seller.name,
                'country': self.seller.country
            },
            'language': self.language,
            'price': self.price,
            'quantity': self.quantity,
            'condition': self.condition,
            'comment': self.comment,
            'date': date_float,
            'ended': self.ended,
            'new': self.new,
            'previous_prices': prev_prices_normalized,
            'previous_quantities': prev_qtys_normalized,
            'first_date': first_date_float,
            'last_date': last_date_float,
            'price_is_new': self.price_is_new,
            'quantity_change': self.quantity_change,
            'first_ed': self.first_ed,
            'reverse_holo': self.reverse_holo,
            'archived': self.archived,
            'grade_company': self.grade_company,
            'grade': self.grade,
            'grade_source': self.grade_source
        }

    def from_json(self, data):
        """Load listing from JSON dictionary."""
        self.card = data.get('card', '')
        self.canonical_name = data.get('canonical_name', '')

        seller_data = data.get('seller', {})
        self.seller = Seller()
        self.seller.name = seller_data.get('name', '')
        self.seller.country = seller_data.get('country', '')

        self.language = data.get('language', '')
        self.price = data.get('price', 0.0)
        self.quantity = data.get('quantity', 0)
        self.condition = data.get('condition', '')
        self.comment = data.get('comment', '')
        # Normalize date to float for consistency
        date_val = data.get('date', '')
        self.date = float(date_val) if date_val else 0.0
        self.ended = data.get('ended', False)
        self.new = data.get('new', True)
        # Normalize previous_prices: ensure price and date are floats
        raw_prices = data.get('previous_prices', [])
        self.previous_prices = []
        for entry in raw_prices:
            if len(entry) >= 2:
                price = float(entry[0]) if entry[0] else 0.0
                date = float(entry[1]) if entry[1] else 0.0
                self.previous_prices.append((price, date))
        raw_qtys = data.get('previous_quantities', [])
        self.previous_quantities = []
        for entry in raw_qtys:
            if len(entry) >= 2:
                qty = int(entry[0]) if entry[0] is not None else 0
                date = float(entry[1]) if entry[1] else 0.0
                self.previous_quantities.append((qty, date))
        # Normalize first_date and last_date to float
        first_date_val = data.get('first_date', '')
        self.first_date = float(first_date_val) if first_date_val else 0.0
        last_date_val = data.get('last_date', '')
        self.last_date = float(last_date_val) if last_date_val else 0.0
        self.price_is_new = data.get('price_is_new', False)
        self.quantity_change = data.get('quantity_change', 0)
        self.first_ed = data.get('first_ed', 2)
        self.reverse_holo = data.get('reverse_holo', 2)
        self.archived = data.get('archived', False)
        # Defaults to None, not "": a page written before grading support has
        # never been checked, which is a different thing from "checked, no grade"
        # and is what lets update_page() treat old listings as a match wildcard.
        self.grade_company = data.get('grade_company', None)
        grade_val = data.get('grade', None)
        self.grade = float(grade_val) if grade_val is not None else None
        self.grade_source = data.get('grade_source', None)

    def parse_from_row(self,row):
        self.seller.name = row.find('span',attrs={'class':'seller-name'}).find('a').text
        seller_name_icon = row.find('span',attrs={'class':'seller-name'}).find('span',attrs={'class':'icon d-flex has-content-centered me-1'})
        location_label = tooltip_label(seller_name_icon)
        self.seller.country = location_to_english.get(location_label, location_label) if location_label else ""
        condition = row.find('a',attrs={'class':'article-condition'})
        if condition:
            self.condition = condition.find('span',attrs={'class':'badge'}).text
        else:
            self.condition = "NM"
        card_language_icon =row.find('div',attrs={'class':'product-attributes'}).find('span',attrs={'class':'icon me-2'})
        language_label = tooltip_label(card_language_icon)
        self.language = language_to_english.get(language_label, language_label) if language_label else ""
        comment_section = row.find('div',attrs={'class':'product-comments'})
        if comment_section:
            self.comment = comment_section.find('span',attrs={'class':'text-truncate'}).text.replace(",",".")
        self.price = float(row.find('div',attrs={'class':'price-container'}).find('span',attrs={'class':'text-nowrap'}).text.split()[0].replace('.','').replace(',','.'))
        self.quantity = int(row.find('div',attrs={'class':'amount-container'}).find('span',attrs={'class':'item-count'}).text)
        if row.find("span",attrs={'class':'icon','aria-label':'First Edition'}):
            self.first_ed = 1
        else:
            self.first_ed = 0
        if row.find("span", attrs={'class':'icon', 'aria-label':'Reverse Holo'}):
            self.reverse_holo = 1
        else:
            self.reverse_holo = 0
        # CardMarket has no field for professional grading; sellers write it into
        # the comment, so that is where it has to come from.
        self.apply_parsed_grade()

    def build_row(self):
        date = self.date if self.ended else self.first_date
        first_date_str = datetime.fromtimestamp(float(self.first_date)).strftime('%d.%m.%Y') if self.first_date else ""
        display_quantity = (-self.quantity_change) if (self.ended and self.quantity_change < 0) else self.quantity
        qty_history = []
        for qty, ts in self.previous_quantities:
            try:
                qty_history.append([int(qty), datetime.fromtimestamp(float(ts)).strftime('%d.%m.%Y')])
            except (ValueError, TypeError, OSError):
                pass
        if not self.ended:
            try:
                qty_history.append([self.quantity, datetime.fromtimestamp(float(self.date)).strftime('%d.%m.%Y')])
            except (ValueError, TypeError, OSError):
                pass
        qty_history_json = json.dumps(qty_history)

        price_history_arr = []
        for p, ts in self.previous_prices:
            try:
                price_history_arr.append([float(p), datetime.fromtimestamp(float(ts)).strftime('%d.%m.%Y')])
            except (ValueError, TypeError, OSError):
                pass
        try:
            price_history_arr.append([self.price, datetime.fromtimestamp(float(self.date)).strftime('%d.%m.%Y')])
        except (ValueError, TypeError, OSError):
            pass
        price_history_json = json.dumps(price_history_arr)
        status = ""
        row_extra_style = ""

        # Archived listings get a distinct muted style
        if self.archived:
            status = " style=\"background:repeating-linear-gradient(45deg, #f5f5f5, #f5f5f5 10px, #e8e8e8 10px, #e8e8e8 20px); opacity: 0.7;\""
            row_extra_style = " archived-listing"
        elif self.ended:
            status = " style=\"background:gray;\""
            gray = [128,128,128]
            red = [220,20,60]
            diff = max(0,(10-math.floor((time.time() - float(date))/(24*60*60))))/10
            new = [gray[i]*(1-diff)+red[i]*diff for i in range(3)]
            status = " style=\"background:rgb("+str(new[0])+","+str(new[1])+","+str(new[2])+");\""
        else:
            if self.quantity_change < 0:
                status = " style=\"background:orange;\""
            elif self.quantity_change > 0:
                status = " style=\"background:greenyellow;\""
            else:
                diff = max(0,(10-math.floor((time.time() - float(date))/(24*60*60))))/10
                status = " style=\"background:rgba(34,139,34,"+str(diff)+");\""
    
        quantity_string = str(self.quantity) + (("(" + str(self.quantity-self.quantity_change) + ")") if self.quantity_change else "")
    
        price_style = ""
        # Direction of the last price move, applied as a class so dark.css can
        # theme it (an inline color would be a colored-text-on-colored-status-bg
        # contrast trap, e.g. dark green on a green "new listing" row).
        price_class = ""
        price_string = str(self.price).replace('.',',') + ("0" if len(str(self.price).split('.')[1]) == 1 else "")
        if len(self.previous_prices) > 0:
            price_class = "price-down" if self.price < float(self.previous_prices[-1][0]) else "price-up"
            price_string += " (" + str(self.previous_prices[-1][0]).replace('.',',') + ("0" if len(str(self.previous_prices[-1][0]).split('.')[1]) == 1 else "") + ")"
            list_of_previous_prices = ""
            for prev_price in self.previous_prices:
                prev_price_date = "            "
                try:
                    float_date = float(prev_price[1]) if prev_price[1] else 0
                    if float_date > 17000000:
                        prev_price_date = datetime.fromtimestamp(float_date).date()
                except (ValueError, TypeError):
                    pass
                list_of_previous_prices += f"{prev_price_date} {prev_price[0]}€\n"
            price_string+= "€"
            price_string = f"<span title=\"{list_of_previous_prices}\">{price_string}</span>"
        else:
            price_string += "€"

        # Add strikethrough for archived listings
        if self.archived:
            price_string = f"<s style=\"opacity: 0.6;\">{price_string}</s>"
            price_style = " style=\"color: #999 !important;\" "
            price_class = ""

        first_edition_marker = ""
        first_edition_hider = "none"
        if self.first_ed == 1:
            first_edition_hider = "is"
            first_edition_marker = """
            <span style="display: inline-block; width: 16px; height: 16px; background-image:url('static/Blanko/ssMain2.png'); background-position: -112px -16px;" data-original-title="First Edition" data-bs-html="true" data-bs-placement="bottom" class="icon st_SpecialIcon mr-1" aria-label="First Edition" data-bs-original-title="First Edition"></span>"""
        # The grade badge goes in the comment column rather than next to the
        # first-ed/reverse-holo icons: product-attributes is a fixed 6.5rem and
        # already holds four items, and "PSA 10" is text, not a 16px sprite.
        grade_marker = ""
        grade_hider = "none"
        grade_value_hider = "none"
        if self.is_graded():
            grade_hider = self.grade_company.lower()
            grade_value_hider = grade_slug(self.grade)
            grade_label = self.grade_label()
            grade_marker = ("<span class=\"grade-badge me-1\" style=\"background:" +
                            COMPANY_COLORS.get(self.grade_company, "#6c757d") +
                            ";\" title=\"Graded " + grade_label + "\">" +
                            grade_label + "</span>")

        reverse_holo_marker = ""
        reverse_holo_hider = "none"
        if self.reverse_holo == 1:
            reverse_holo_hider = "is"
            reverse_holo_marker = """
            <span style="display: inline-block; width: 16px; height: 16px; background-image:url('static/Blanko/ssMain2.png'); background-position: -416px -16px;" data-original-title="Reverse Holo" data-bs-html="true" data-bs-placement="bottom" class="icon st_SpecialIcon mr-1" aria-label="Reverse Holo" data-bs-original-title="Reverse Holo"></span>"""

        table_element = ("<div id=\"articleRow1575860637\" " + \
                            "class=\"show-" + self.seller.country[15:] +\
                            " language-" + self.language +\
                            " availability-" + str(not self.ended) +\
                            " condition-" + self.condition.lower() + "-val" +\
                            " firsted-" + first_edition_hider +\
                            " reverseholo-" + reverse_holo_hider +\
                            " grade-" + grade_hider +\
                            " gradeval-" + grade_value_hider +\
                            row_extra_style +\
                            " row g-0 article-row\"" + \
                    " data-grade-company=\"" + (self.grade_company or "") + "\"" + \
                    " data-grade=\"" + (grade_slug(self.grade) if self.is_graded() else "") + "\"" + \
                    " data-first-date=\"" + first_date_str + "\"" + \
                    " data-is-ended=\"" + str(self.ended).lower() + "\"" + \
                    " data-quantity=\"" + str(display_quantity) + "\"" + \
                    " data-price=\"" + str(self.price) + "\"" + \
                    " data-qty-history='" + qty_history_json + "'" + \
                    " data-price-history='" + price_history_json + "'" + \
                    ">" + \
                            "<div class=\"d-none col\">" + \
                            "</div>" + \
                            "<div class=\"col-sellerProductInfo col\">" + \
                            "<div class=\"row g-0\">" + \
                                "<div class=\"col-seller col-12 col-lg-auto\">" + \
                                    "<span class=\"seller-info d-flex align-items-center\">" + \
                                        "<span class=\"seller-name d-flex\">" + \
                                            "<span data-bs-toggle=\"tooltip\" data-bs-html=\"true\" data-bs-placement=\"bottom\" class=\"icon d-flex has-content-centered me-1\" aria-label=\"Artikelstandort: Deutschland\" data-bs-original-title=\"Artikelstandort: Deutschland\">" + \
                                                "<span style=\"display: inline-block; width: 16px; height: 16px; background-image:url('static/Blanko/ssMain.png'); background-position: " + \
                                                    (flags[location_to_english[self.seller.country]] if self.seller.country in location_to_english and location_to_english[self.seller.country] in flags else "0px 0px") + \
                                                    ";\" class=\"icon\"></span>" + \
                                                    (self.seller.country if self.seller.country not in location_to_english or location_to_english[self.seller.country] not in flags else "") + \
                                                "</span>" + \
                                                "<span class=\"d-flex has-content-centered me-1\">" + \
                                                    "<a href=\"\">" +\
                                                        self.seller.name + \
                                                    "</a>" + \
                                                "</span>" + \
                                            "</span>" + \
                                        "</span>" + \
                                    "</div>" + \
                                    "<div class=\"col-product col-12 col-lg\">" + \
                                        "<div class=\"row g-0\">" + \
                                            "<div class=\"product-attributes\" style=\"flex: 0 0 6.5rem;\">" + \
                                                "<a data-bs-placement=\"bottom\" class=\"article-condition condition-" + \
                                                self.condition.lower() + \
                                                " me-1\" data-bs-original-title=\"" + \
                                                condition_long[self.condition] +\
                                                "\">" + \
                                                    "<span class=\"badge \">" + \
                                                        self.condition +\
                                                    "</span>" + \
                                                "</a>" + \
                                                "<span style=\"display: inline-block; width: 16px; height: 16px; background-image:url('static/Blanko/ssMain2.png'); background-position: " + \
                                                    (language_flags[language_to_english[self.language]] if self.language in language_to_english and language_to_english[self.language] in language_flags else "") + \
                                                    ";\" data-original-title=\"Englisch\" data-bs-toggle=\"tooltip\" data-bs-html=\"true\" data-bs-placement=\"bottom\" class=\"icon me-2\" aria-label=\"Englisch\" data-bs-original-title=\"Englisch\">" + \
                                                    ("" if self.language in language_to_english and language_to_english[self.language] in language_flags else self.language) +\
                                                "</span>" + \
                                                first_edition_marker + \
                                                reverse_holo_marker + \
                                            "</div>" + \
                                            "<div class=\"product-comments me-1 col\">" + \
                                                "<div class=\"w-100 d-flex align-items-center\">" + \
                                                    grade_marker + \
                                                    "<span class=\"d-block text-truncate text-muted fst-italic small\" title=\"" + \
                                                        self.comment.replace('"', '&quot;') + \
                                                    "\">" + \
                                                        self.comment + \
                                                    "</span>" + \
                                                "</div>" + \
                                            "</div>" + \
                                        "</div>" + \
                                    "</div>" + \
                                "</div>" + \
                            "</div>" + \
                            "<div class=\"col-offer col-auto\""+status+">" + \
                                "<div style=\"width:10rem\" class=\"price-container d-flex justify-content-end\">" + \
                                    "<div class=\"d-flex flex-column\">" + \
                                        "<div class=\"d-flex align-items-center justify-content-end\">" + \
                                            "<span class=\"color-primary small text-end text-nowrap fw-bold " + price_class + "\" " + price_style + ">" + \
                                                price_string +\
                                            "</span>" + \
                                        "</div>" + \
                                    "</div>" + \
                                "</div>" + \
                                "<div class=\"amount-container d-flex justify-content-end me-3\">" + \
                                    "<span class=\"item-count small text-end\">" + \
                                        quantity_string + \
                                    "</span>" + \
                                "</div>" + \
                                "<div class=\"actions-container d-flex align-items-center justify-content-end col ps-2 pe-0\">" + \
                                    "<span>"+
                                        datetime.fromtimestamp(float(date)).strftime('%d.%m.%Y')+
                                    "</span>" + \
                                "</div>" + \
                            "</div>" + \
                            "<div class=\"col-auto d-flex align-items-center\">" +\
                                    "<a href=\"#\" class=\"edit-grade-btn me-1\" title=\"Set grade\"" +\
                                    " data-row=\"" + str(self.row_number) + "\"" +\
                                    " data-company=\"" + (self.grade_company or "") + "\"" +\
                                    " data-grade=\"" + (str(self.grade) if self.grade is not None else "") + "\">" +\
                                    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"22\" height=\"22\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"#666\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M12 20h9\"/><path d=\"M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z\"/></svg>" +\
                                    "</a>" +\
                                    ("<a href=\"?name="+self.canonical_name+".json&unarchive="+str(self.row_number)+"\" title=\"Unarchive\">" +\
                                    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"22\" height=\"22\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"#28a745\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z\"/><line x1=\"12\" y1=\"11\" x2=\"12\" y2=\"17\"/><polyline points=\"9 14 12 11 15 14\"/></svg>" +\
                                    "</a>"
                                    if self.archived else
                                        "<a href=\"?name="+self.canonical_name+".json&archive="+str(self.row_number)+"\" title=\"Archive\">" +\
                                        "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"22\" height=\"22\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"#666\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><path d=\"M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z\"/><line x1=\"12\" y1=\"17\" x2=\"12\" y2=\"11\"/><polyline points=\"9 14 12 17 15 14\"/></svg>" +\
                                    "</a>") +\
                                    "<a href=\"?name="+self.canonical_name+".json&delete="+str(self.row_number)+"\" class=\"ms-1\" title=\"Delete\">" +\
                                    "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"22\" height=\"22\" viewBox=\"0 0 24 24\" fill=\"none\" stroke=\"#dc3545\" stroke-width=\"2\" stroke-linecap=\"round\" stroke-linejoin=\"round\"><polyline points=\"3 6 5 6 21 6\"/><path d=\"M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2\"/><line x1=\"10\" y1=\"11\" x2=\"10\" y2=\"17\"/><line x1=\"14\" y1=\"11\" x2=\"14\" y2=\"17\"/></svg>" +\
                                    "</a>" +\
                            "</div>"
                        "</div>")
        return table_element

    def parse_cardwatcher_from_row(self,row):
        self.seller.name = row.find('span',attrs={'class':'seller-name'}).find('a').text.replace(' ','').replace('\t','')
        country_flag = row.find('span',attrs={'class':'seller-name'}).find('span',attrs={'class':'icon'}).find('span')['style'].split(':')[-1][1:-1]
        for flag in flags:
            if flags[flag] == country_flag:
                self.seller.country = flag
        condition = row.find('a',attrs={'class':'article-condition'})
        if condition:
            self.condition = condition.find('span',attrs={'class':'badge'}).text.replace(' ','').replace('\t','')
        else:
            self.condition = "NM"
        language_flag = row.find('div',attrs={'class':'product-attributes'}).find('span',attrs={'class':'icon'})['style'].split(':')[-1][1:-1]
        for flag in language_flags:
            if language_flags[flag] == language_flag:
                self.language = flag
        comment_section = row.find('div',attrs={'class':'product-comments'})
        if comment_section:
            self.comment = comment_section.find('span',attrs={'class':'text-truncate'}).text.replace(",",".")
            while len(self.comment) > 0 and (self.comment[-1] == ' ' or self.comment[-1] == '\t'):
                self.comment = self.comment[:-1]
        price_string = row.find('div',attrs={'class':'price-container'}).find('span',attrs={'class':'text-nowrap'}).text.replace(' ','')
        print(price_string)
        if '(' in price_string:
            self.price = float(price_string.split('(')[0].replace('.','').replace(',','.'))
            self.previous_prices.append(float(price_string.split('(')[1][:-2].replace('.','').replace(',','.')))
        else:
            self.price = float(price_string[:-1].replace('.','').replace(',','.'))
        quantity_string = row.find('div',attrs={'class':'amount-container'}).find('span',attrs={'class':'item-count'}).text
        if '(' in quantity_string:
            self.quantity = int(quantity_string.split('(')[0])
        else:
            self.quantity = int(quantity_string)
        self.first_ed = 0
        if self.quantity == 0:
            self.ended = True
        date_string = row.find('div',attrs={'class':'actions-container'}).text.split('.')
        self.date = datetime(int(date_string[-1]),int(date_string[1]),int(date_string[0])).timestamp()
        
