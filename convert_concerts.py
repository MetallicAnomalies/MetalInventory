import json
import csv
from collections import defaultdict
from datetime import datetime

# ---------------------------------------------------------------------------
# Location helper: map (city, country) → "City, Province/State, Country"
# per ConcertArchives.com Location Field guidelines.
#
# The first-level administrative division must be included; for Netherlands
# those are provinces, for Belgium provinces, for Germany states (Länder),
# for France regions, for Israel districts, etc.
# ---------------------------------------------------------------------------

CITY_PROVINCE = {
    # Netherlands – provinces
    ("Amsterdam",   "Netherlands"): ("Amsterdam",   "North Holland",     "Netherlands"),
    ("Arnhem",      "Netherlands"): ("Arnhem",       "Gelderland",        "Netherlands"),
    ("Den Bosch",   "Netherlands"): ("Den Bosch",    "North Brabant",     "Netherlands"),
    ("Drachten",    "Netherlands"): ("Drachten",     "Friesland",         "Netherlands"),
    ("Eindhoven",   "Netherlands"): ("Eindhoven",    "North Brabant",     "Netherlands"),
    ("Enschede",    "Netherlands"): ("Enschede",     "Overijssel",        "Netherlands"),
    ("Groningen",   "Netherlands"): ("Groningen",    "Groningen",         "Netherlands"),
    ("Haarlem",     "Netherlands"): ("Haarlem",      "North Holland",     "Netherlands"),
    ("Hengelo",     "Netherlands"): ("Hengelo",      "Overijssel",        "Netherlands"),
    ("Leeuwarden",  "Netherlands"): ("Leeuwarden",   "Friesland",         "Netherlands"),
    ("Nijmegen",    "Netherlands"): ("Nijmegen",     "Gelderland",        "Netherlands"),
    ("Rotterdam",   "Netherlands"): ("Rotterdam",    "South Holland",     "Netherlands"),
    ("Sneek",       "Netherlands"): ("Sneek",        "Friesland",         "Netherlands"),
    ("The Hague",   "Netherlands"): ("The Hague",    "South Holland",     "Netherlands"),
    ("Tilburg",     "Netherlands"): ("Tilburg",      "North Brabant",     "Netherlands"),
    ("Utrecht",     "Netherlands"): ("Utrecht",      "Utrecht",           "Netherlands"),
    ("Zwolle",      "Netherlands"): ("Zwolle",       "Overijssel",        "Netherlands"),
    # Belgium – provinces
    ("Dessel",      "Belgium"):     ("Dessel",       "Antwerp",           "Belgium"),
    # Germany – states (Länder)
    ("Wacken",      "Germany"):     ("Wacken",       "Schleswig-Holstein","Germany"),
    ("Dinkelsbühl", "Germany"):     ("Dinkelsbühl",  "Bavaria",           "Germany"),
    ("Clisson",     "France"):      ("Clisson",      "Pays de la Loire",  "France"),
    # Israel – districts
    ("Tel Aviv",    "Israel"):      ("Tel Aviv",     "Tel Aviv",          "Israel"),
    ("Givat Brenner","Israel"):     ("Givat Brenner","Central",           "Israel"),
}


def get_location(city: str, country: str) -> str:
    """Return 'City, Division, Country' using the lookup table, or fall
    back to 'City, Country' if the city is not mapped yet."""
    key = (city, country)
    if key in CITY_PROVINCE:
        c, prov, cntry = CITY_PROVINCE[key]
        return f"{c}, {prov}, {cntry}"
    # Fallback – log a warning so you know what to add to the table
    print(f"  [WARNING] No province/state mapping for: {city!r}, {country!r} – "
          f"using '{city}, {country}'")
    return f"{city}, {country}" if country else city


# ---------------------------------------------------------------------------
# Event-name classification
#
# ConcertArchives 'Concert Title or Tour Title' should contain ONLY the
# official concert or tour title.  Descriptive strings such as
# "Support for X", "Special Guest for X", "Co-Headline with X",
# "Double Bill with X", etc. are NOT official titles → leave the field blank.
# The default title will then be auto-generated from the band list.
# ---------------------------------------------------------------------------

import re

# Patterns whose presence means the event string is NOT an official title
_NON_TITLE_PATTERNS = [
    r"^support\s+for\b",
    r"^special\s+guest\s+for\b",
    r"^co-headline\s+with\b",
    r"^double\s+bill\s+with\b",
    r"^opening\s+for\b",
]
_NON_TITLE_RE = re.compile(
    "|".join(_NON_TITLE_PATTERNS), re.IGNORECASE
)


def is_official_title(event_name: str) -> bool:
    """Return True if event_name looks like a real concert/tour title."""
    if not event_name:
        return False
    return _NON_TITLE_RE.search(event_name) is None


def convert():
    try:
        with open("concerts_metadata.json", "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print("concerts_metadata.json not found")
        return

    # ------------------------------------------------------------------
    # Group bands that share the same (date, venue, location) key.
    # For the Concert Title we keep the *first* official title seen for
    # that key (earlier entries take precedence).
    # ------------------------------------------------------------------
    concerts: dict[tuple, list[str]] = defaultdict(list)
    concert_titles: dict[tuple, str] = {}

    for band, events in data.items():
        for ev in events:
            date_str = ev.get("date", "")
            try:
                # Convert DD/MM/YYYY → MM/DD/YYYY (ConcertArchives format)
                date_obj = datetime.strptime(date_str, "%d/%m/%Y")
                formatted_date = date_obj.strftime("%m/%d/%Y")
            except ValueError:
                formatted_date = date_str

            venue    = ev.get("venue", "")
            city     = ev.get("city", "")
            country  = ev.get("country", "")
            end_date = ev.get("endDate", "")
            event_name = ev.get("event", "")

            location = get_location(city, country)

            # Format end date the same way if present
            formatted_end_date = ""
            if end_date:
                try:
                    end_obj = datetime.strptime(end_date, "%d/%m/%Y")
                    formatted_end_date = end_obj.strftime("%m/%d/%Y")
                except ValueError:
                    formatted_end_date = end_date

            key = (formatted_date, formatted_end_date, venue, location)

            if band not in concerts[key]:
                concerts[key].append(band)

            # Store title only if it is an official one and none stored yet
            if key not in concert_titles:
                concert_titles[key] = event_name if is_official_title(event_name) else ""
            elif not concert_titles[key] and is_official_title(event_name):
                concert_titles[key] = event_name

    # ------------------------------------------------------------------
    # Write CSV
    # Columns: Start Date, End Date, Concert Name, Bands, Venue, Location
    # ------------------------------------------------------------------
    with open("concerts_import.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Start Date", "End Date", "Concert Name", "Bands", "Venue", "Location"])

        for (date, end_date, venue, location), bands in concerts.items():
            bands_str  = " / ".join(bands)
            title      = concert_titles.get((date, end_date, venue, location), "")
            writer.writerow([date, end_date, title, bands_str, venue, location])

    print(f"Done – {len(concerts)} concert(s) written to concerts_import.csv")


if __name__ == "__main__":
    convert()