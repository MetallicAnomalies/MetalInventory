"""
enrich.py â€” Enrich shard metadata from Discogs XML data dumps.

Usage:
    python enrich.py

Reads ./output/shards/base-{xx}.json (256 buckets) and overwrites them in-place.
Reads ./output/cache-manifest.json and updates the builtAt timestamp.
Writes ./output/enrichment-report.json with statistics and warnings.

Only Python standard library is used.
"""

import gzip
import hashlib
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# Constants â€” edit these to point at your local Discogs dump files
# ---------------------------------------------------------------------------
DISCOGS_RELEASES_DUMP_PATH = "./discogs_20260501_releases.xml.gz"
DISCOGS_MASTERS_DUMP_PATH = "./discogs_20260501_masters.xml.gz"
DISCOGS_ARTISTS_DUMP_PATH  = "./discogs_20260501_artists.xml.gz"

SHARDS_DIR       = "./output/shards"
MANIFEST_PATH    = "./output/cache-manifest.json"
REPORT_PATH      = "./output/enrichment-report.json"

PROFILE_MAX_CHARS = 500
PROGRESS_INTERVAL = 100_000

# ---------------------------------------------------------------------------
# Metal style detection â€” exact-match set + endswith("metal") catch-all
# ---------------------------------------------------------------------------
METAL_STYLE_KEYWORDS: set[str] = {
    "metal", "doom", "sludge", "drone metal", "grindcore",
    "crust", "black metal", "death metal", "thrash", "heavy metal",
    "power metal", "prog metal", "speed metal", "folk metal",
    "gothic metal", "symphonic metal", "metalcore", "deathcore",
    "post-metal", "stoner metal", "nu-metal", "glam metal",
    "hair metal", "industrial metal", "brutal death metal",
    "technical death metal", "melodic death metal",
}

GENERIC_METAL: set[str] = {"metal"}


def is_metal_style(style: str) -> bool:
    s = style.lower().strip()
    return s in METAL_STYLE_KEYWORDS or s.endswith('metal') or s == 'heavy metal'


# ---------------------------------------------------------------------------
# Compressed-XML helper
# ---------------------------------------------------------------------------
def open_xml(path: str):
    """Return a file-like object for an XML or gzipped-XML file."""
    if path.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


# ---------------------------------------------------------------------------
# Name normalisation & shard key
# ---------------------------------------------------------------------------
def normalize(name: str) -> str:
    # Unicode-aware: keep letters/digits from any script, strip accents/marks and punctuation.
    folded = unicodedata.normalize("NFKD", name).casefold()
    out: list[str] = []
    for ch in folded:
        if unicodedata.category(ch) == "Mn":
            continue
        if ch.isalnum():
            out.append(ch)
    normalized = "".join(out)
    # Avoid collapsing purely-symbol names (e.g. ".", "...?") into the same empty key.
    return normalized or hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]


def shard_key(normalized_name: str) -> str:
    return hashlib.sha256(normalized_name.encode()).hexdigest()[:2]

_EDITION_STRIP = re.compile(
    r"[\(\[â€\-â€“â€”]?\s*("
    r"deluxe|expanded|anniversary|remaster(ed)?|reissue|limited|special"
    r"|collector'?s?|bonus|super|ultimate|definitive|platinum|gold"
    r"|\d+(th|st|nd|rd)\s+anniversary"
    r")\b.*",
    re.IGNORECASE,
)

def normalize_album_key(title: str) -> str:
    """Strip edition markers before normalizing â€” for dedup keys only."""
    stripped = _EDITION_STRIP.sub("", title).strip(" ([")
    return normalize(stripped)


_NOISY_EDITION_PATTERN = re.compile(
    r"(?:\s*[\(\[\-:]\s*|\s+)(deluxe|expanded|anniversary|remaster(ed)?|reissue|limited|special|collector'?s?|bonus|super|ultimate|definitive|platinum|gold|edition|version|issue|hd\s+upgrade|4k\s+upgrade|full\s+version|\d+(th|st|nd|rd)\s+anniversary)\b.*",
    re.IGNORECASE,
)
_NOISY_RELEASE_TEXT_PATTERN = re.compile(
    r"\b("
    r"deluxe|expanded|anniversary|remaster(?:ed)?|reissue(?:d)?|re-record(?:ed|ing)?"
    r"|rerecord(?:ed|ing)?|redux|revisited|special edition|collector'?s edition"
    r"|limited edition|bonus disc|bonus cd|bonus tracks?|alternate version"
    r"|alternate take|new version|new recording|new mix|reconstructed|reimagined"
    r"|reimagining|reworked|revisited|202\d mix|201\d mix"
    r")\b",
    re.IGNORECASE,
)
_EXCLUDED_RELEASE_PATTERN = re.compile(
    r"\b(single|live|demo|acoustic|instrumental|unplugged|split|bootleg|compilation|greatest hits|best of|anthology|sampler|interview|remix|remixes|karaoke|a cappella|instrumentals?)\b",
    re.IGNORECASE,
)
_EP_PATTERN = re.compile(r"\bep\b", re.IGNORECASE)


OFFICIAL_ALBUM_DESCRIPTIONS = {"Album", "LP", "CD", "Cassette"}
FORBIDDEN_ALBUM_DESCRIPTIONS = {
    "single", "maxi-single", "promo", "compilation", "live", "demo",
    "mixtape/street", "interview", "bootleg", "sampler"
}


def is_relevant_release_title(title: str) -> bool:
    return bool(_EP_PATTERN.search(title)) or not _EXCLUDED_RELEASE_PATTERN.search(title)


def album_preference_score(title: str) -> int:
    score = len(title)
    if not is_relevant_release_title(title):
        score += 10_000
    if _NOISY_EDITION_PATTERN.search(title):
        score += 1_000
    if _EXCLUDED_RELEASE_PATTERN.search(title) and not _EP_PATTERN.search(title):
        score += 500
    return score

def collect_release_descriptions(elem) -> list[str]:
    descriptions: list[str] = []
    for fmt in elem.findall("./formats/format"):
        fmt_name = (fmt.get("name") or "").strip()
        if fmt_name:
            descriptions.append(fmt_name)
        for desc in fmt.findall("./descriptions/description"):
            d_text = (desc.text or "").strip()
            if d_text:
                descriptions.append(d_text)
    return descriptions


def collect_release_variant_text(elem) -> str:
    title = (elem.findtext("title") or "").strip()
    notes = (elem.findtext("notes") or "").strip()
    descriptions = " ".join(collect_release_descriptions(elem))
    return " ".join(part for part in (title, descriptions, notes) if part)


def is_original_issue_candidate(elem) -> bool:
    return not _NOISY_RELEASE_TEXT_PATTERN.search(collect_release_variant_text(elem))


def release_sort_date(elem) -> str:
    released = (elem.findtext("released") or "").strip()
    if released:
        parts = released.split("-")
        if len(parts) == 3 and parts[0].isdigit():
            year = parts[0]
            month = parts[1] if parts[1].isdigit() and parts[1] != "00" else "99"
            day = parts[2] if parts[2].isdigit() and parts[2] != "00" else "99"
            return f"{year}-{month}-{day}"

    year = clean_release_year(elem.findtext("year") or elem.get("year"))
    if year:
        return f"{year}-99-99"
    return "9999-99-99"


def extract_release_year(elem) -> str | None:
    """
    Extract the best available 4-digit year from a Discogs release element.

    Discogs sometimes leaves <year> empty or set to 0 while <released> still
    contains a usable YYYY-MM-DD date. Use <year> first, then fall back to the
    year embedded in <released>.
    """
    year = clean_release_year(elem.findtext("year") or elem.get("year"))
    if year:
        return year
    return clean_release_year(elem.findtext("released"))


def release_preference_score(elem, title: str) -> tuple[int, str, int, int]:
    descriptions = collect_release_descriptions(elem)
    special_descriptions = {
        d.lower()
        for d in descriptions
        if d and d.lower() not in {"album", "ep", "mini-album", "lp", "cd", "cassette", "stereo", "mono"}
    }
    noisy_penalty = 1 if not is_original_issue_candidate(elem) else 0
    format_penalty = len(special_descriptions)
    return (
        noisy_penalty,
        release_sort_date(elem),
        format_penalty,
        album_preference_score(title),
    )


def is_official_catalog_release(elem) -> bool:
    """True for official album/EP releases. False for singles, live, splits and noisy editions."""
    # Check artist count (ignore splits)
    artists = elem.findall("./artists/artist")
    if len(artists) != 1:
        return False

    title = (elem.findtext("title") or "").strip()
    if not title or not is_relevant_release_title(title):
        return False

    is_album = False
    for fmt in elem.findall("./formats/format"):
        for desc in fmt.findall("./descriptions/description"):
            d_text = (desc.text or "").strip().lower()
            if d_text in FORBIDDEN_ALBUM_DESCRIPTIONS:
                return False
            if d_text in {"album", "ep", "mini-album"}:
                is_album = True

    return is_album

# ---------------------------------------------------------------------------
# Profile truncation
# ---------------------------------------------------------------------------
def truncate_profile(text: str, max_chars: int = PROFILE_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_space = truncated.rfind(" ")
    return truncated[:last_space] if last_space > 0 else truncated


# ---------------------------------------------------------------------------
# Genre / style normalisation
# ---------------------------------------------------------------------------
_SHORTHAND_METAL = {
    # Core / Traditional
    "heavy": "Heavy Metal",
    "speed": "Speed Metal",
    "thrash": "Thrash Metal",
    "death": "Death Metal",
    "black": "Black Metal",
    "doom": "Doom Metal",
    "power": "Power Metal",
    "glam": "Glam Metal",
    "hair": "Glam Metal",

    # Doom variants
    "sludge": "Sludge Metal",
    "stoner": "Stoner Metal",
    "funeral doom": "Funeral Doom Metal",
    "death doom": "Death Doom Metal",
    "gothic doom": "Gothic Doom Metal",
    "drone doom": "Drone Doom Metal",
    "epic doom": "Epic Doom Metal",

    # Death variants
    "melodic death": "Melodic Death Metal",
    "melodeath": "Melodic Death Metal",
    "brutal death": "Brutal Death Metal",
    "technical death": "Technical Death Metal",
    "tech death": "Technical Death Metal",
    "tech-death": "Technical Death Metal",
    "progressive death": "Progressive Death Metal",
    "slam death": "Slam Death Metal",
    "slam": "Slam Death Metal",
    "old school death": "Old School Death Metal",
    "ossdm": "Old School Death Metal",

    # Black variants
    "raw black": "Raw Black Metal",
    "atmospheric black": "Atmospheric Black Metal",
    "atmo black": "Atmospheric Black Metal",
    "symphonic black": "Symphonic Black Metal",
    "melodic black": "Melodic Black Metal",
    "post-black": "Post-Black Metal",
    "post black": "Post-Black Metal",
    "depressive black": "Depressive Black Metal",
    "dsbm": "Depressive Black Metal",
    "ambient black": "Ambient Black Metal",
    "pagan black": "Pagan Black Metal",
    "war black": "War Black Metal",
    "bestial black": "Bestial Black Metal",
    "blackgaze": "Blackgaze",

    # Blackened crossovers
    "blackened": "Blackened Metal",
    "blackened death": "Blackened Death Metal",
    "blackened thrash": "Blackened Thrash Metal",
    "blackened doom": "Blackened Doom Metal",

    # Thrash variants
    "groove": "Groove Metal",
    "crossover thrash": "Crossover Thrash",
    "crossover": "Crossover Thrash",
    "teutonic thrash": "Teutonic Thrash Metal",
    "bay area thrash": "Bay Area Thrash Metal",

    # Progressive / Avant-garde
    "prog": "Progressive Metal",
    "progressive": "Progressive Metal",
    "prog death": "Progressive Death Metal",
    "extreme progressive": "Extreme Progressive Metal",
    "avantgarde": "Avantgarde Metal",
    "avant-garde": "Avantgarde Metal",
    "avant garde": "Avantgarde Metal",
    "experimental": "Experimental Metal",
    "math metal": "Math Metal",
    "math": "Math Metal",
    "djent": "Djent",

    # Atmospheric / Post
    "post-metal": "Post-Metal",
    "post metal": "Post-Metal",
    "atmospheric": "Atmospheric Metal",
    "ambient": "Ambient Metal",

    # Symphonic / Gothic / Orchestral
    "symphonic": "Symphonic Metal",
    "gothic": "Gothic Metal",
    "orchestral": "Orchestral Metal",
    "neoclassical": "Neoclassical Metal",
    "opera metal": "Opera Metal",
    "operatic": "Operatic Metal",
    "symphonic power": "Symphonic Power Metal",

    # Folk / Viking / Pagan
    "folk": "Folk Metal",
    "viking": "Viking Metal",
    "pagan": "Pagan Metal",
    "celtic": "Celtic Metal",
    "medieval": "Medieval Metal",
    "pirate": "Pirate Metal",

    # Industrial / Electronic
    "industrial": "Industrial Metal",
    "cyber": "Cyber Metal",
    "electro": "Electro Metal",
    "nu": "Nu Metal",
    "nu-metal": "Nu Metal",

    # Hardcore crossovers
    "metalcore": "Metalcore",
    "deathcore": "Deathcore",
    "grindcore": "Grindcore",
    "grind": "Grindcore",
    "powerviolence": "Powerviolence",
    "mathcore": "Mathcore",
    "hardcore": "Hardcore Metal",
    "crust": "Crust Metal",
    "d-beat": "D-Beat",

    # Misc / Niche
    "war metal": "War Metal",
    "speed doom": "Speed Doom Metal",
    "epic": "Epic Metal",
    "desert": "Desert Metal",
    "spaghetti": "Spaghetti Metal",
    "psych": "Psychedelic Metal",
    "psychedelic": "Psychedelic Metal",
    "noise": "Noise Metal",
    "drone": "Drone Metal",
    "southern": "Southern Metal",
    "country": "Country Metal",
    "rap metal": "Rap Metal",
    "funk metal": "Funk Metal",
    "jazz metal": "Jazz Metal",
    "nitm": "New Wave of Traditional Heavy Metal",
    "nwothm": "New Wave of Traditional Heavy Metal",
    "nwobhm": "New Wave of British Heavy Metal",
    "nwoahm": "New Wave of American Heavy Metal",

    # Extreme variants
    "extreme": "Extreme Metal",
    "extreme metal": "Extreme Metal",
    "extreme progressive": "Extreme Progressive Metal",
    "extreme prog": "Extreme Progressive Metal",
    "extreme progressive death": "Extreme Progressive Death Metal",
    "extreme prog death": "Extreme Progressive Death Metal",
    "extreme death": "Extreme Death Metal",
    "extreme black": "Extreme Black Metal",
    "extreme doom": "Extreme Doom Metal",
    "extreme gothic": "Extreme Gothic Metal",
    "extreme gothic metal": "Extreme Gothic Metal",
    "extreme symphonic": "Extreme Symphonic Metal",
    "extreme thrash": "Extreme Thrash Metal",
    "extreme power": "Extreme Power Metal",
    "extreme folk": "Extreme Folk Metal",
    "extreme industrial": "Extreme Industrial Metal",
}

def normalise_styles(styles: set[str]) -> list[str]:
    """
    Expand shorthand styles to full metal names (e.g. 'Thrash' -> 'Thrash Metal').
    Drop the generic 'Metal' entry when a more specific metal subgenre is
    present.
    """
    formatted_styles = set()
    for s in styles:
        s_lower = s.lower().strip()
        if s_lower in _SHORTHAND_METAL:
            formatted_styles.add(_SHORTHAND_METAL[s_lower])
        else:
            formatted_styles.add(s)

    lowered = {s.lower(): s for s in formatted_styles}
    has_subgenre = any(
        "metal" in s and s != "metal"
        for s in lowered
    )
    if has_subgenre:
        return sorted(v for k, v in lowered.items() if k != "metal")
    return sorted(lowered.values())


def clean_release_year(year_text: str | None) -> str | None:
    """Extract a stable 4-digit year from Discogs release year text."""
    if not year_text:
        return None
    value = year_text.strip()
    if not value or value == "0":
        return None
    match = re.search(r"\d{4}", value)
    if not match:
        return None
    return match.group(0)


def merge_album_records(
    existing_albums: list[dict] | None,
    new_albums: list[dict],
) -> tuple[list[dict], int]:
    """
    Merge album entries by normalized album name.
    STRICT NO OVERRIDE: Existing albums are sacred. 
    Only truly missing official albums are added.
    """
    album_map: dict[str, dict] = {}
    added_count = 0
    official_keys = {
        normalize_album_key(str(album.get("name", "")).strip())
        for album in new_albums
        if str(album.get("name", "")).strip()
    }

    # Load existing albums
    for album in existing_albums or []:
        name = str(album.get("name", "")).strip()
        if not name:
            continue
        if not is_relevant_release_title(name):
            continue
        key = normalize_album_key(name)
        if not key:
            continue
        if official_keys and key not in official_keys and not _EP_PATTERN.search(name):
            continue
        album_map[key] = album

    # Only add new ones that don't exist
    for album in new_albums:
        name = str(album.get("name", "")).strip()
        if not name:
            continue
        if not is_relevant_release_title(name):
            continue
        key = normalize_album_key(name)
        if not key:
            continue

        if key not in album_map:
            album_map[key] = {
                "name": name,
                "year": str(album.get("year") or ""),
                "genres": sorted({str(g) for g in album.get("genres", []) if str(g).strip()}),
                "_releasePreference": tuple(album.get("_releasePreference", (0, "9999-99-99", 0, album_preference_score(name)))),
            }
            added_count += 1
        else:
            existing = album_map[key]
            incoming_preference = tuple(
                album.get("_releasePreference", (0, "9999-99-99", 0, album_preference_score(name)))
            )
            existing_preference = tuple(
                existing.get(
                    "_releasePreference",
                    (0, "9999-99-99", 0, album_preference_score(str(existing.get("name", "")).strip())),
                )
            )
            if incoming_preference < existing_preference:
                existing["name"] = name
                existing["_releasePreference"] = incoming_preference
            year = str(album.get("year") or "")
            if year and (not existing.get("year") or year < str(existing.get("year"))):
                existing["year"] = year
            existing["genres"] = sorted(
                set(str(g) for g in existing.get("genres", []) if str(g).strip())
                | {str(g) for g in album.get("genres", []) if str(g).strip()}
            )

    merged_albums = sorted(
        (
            {
                k: v
                for k, v in album.items()
                if k != "_releasePreference"
            }
            for album in album_map.values()
        ),
        key=lambda album: (
            album.get("year", ""),
            album.get("name", "").lower(),
        ),
    )
    return merged_albums, added_count


# ---------------------------------------------------------------------------
# Step 0 — Parse masters dump
# ---------------------------------------------------------------------------
def parse_masters(
    masters_path: str,
) -> dict[int, list[dict]]:
    """
    Stream-parse masters.xml.

    Returns:
        artist_master_albums : dict[int, list[dict]] — artist ID -> canonical album list
    """
    artist_master_albums_map: dict[int, dict[str, dict]] = {}

    print(f"[Step 0] Parsing masters: {masters_path}")

    master_count = 0
    context = ET.iterparse(open_xml(masters_path), events=("end",))

    try:
        for _event, elem in context:
            if elem.tag != "master":
                continue

            master_count += 1
            if master_count % PROGRESS_INTERVAL == 0:
                print(f"  ... {master_count:,} masters processed")

            title = (elem.findtext("title") or "").strip()
            if not title:
                elem.clear()
                continue
            if not is_relevant_release_title(title):
                elem.clear()
                continue

            album_key = normalize_album_key(title)
            if not album_key:
                elem.clear()
                continue

            year = clean_release_year(elem.findtext("year")) or ""
            genres = [
                g.text.strip()
                for g in elem.findall("./genres/genre")
                if g.text and g.text.strip()
            ]

            for artist_node in elem.findall("./artists/artist"):
                id_text = (artist_node.findtext("id") or "").strip()
                if not id_text:
                    continue
                try:
                    aid = int(id_text)
                except ValueError:
                    continue

                if aid not in artist_master_albums_map:
                    artist_master_albums_map[aid] = {}

                if album_key not in artist_master_albums_map[aid]:
                    artist_master_albums_map[aid][album_key] = {
                        "name": title,
                        "year": year,
                        "genres": sorted(set(genres)),
                    }
                else:
                    existing = artist_master_albums_map[aid][album_key]
                    existing_year = str(existing.get("year") or "")
                    if year and (not existing_year or year < existing_year):
                        existing["year"] = year
                    existing["genres"] = sorted(
                        set(str(g) for g in existing.get("genres", []) if str(g).strip())
                        | set(genres)
                    )

            elem.clear()

    except (ET.ParseError, gzip.BadGzipFile, EOFError, OSError) as exc:
        print(
            f"  [Step 0] Warning: gzip stream ended early after "
            f"{master_count:,} masters ({type(exc).__name__}: {exc}). "
            "Continuing with data collected so far."
        )

    print(f"[Step 0] Done. {master_count:,} masters parsed.")
    return {
        aid: sorted(
            albums.values(),
            key=lambda album: (
                album.get("year", ""),
                album.get("name", "").lower(),
            ),
        )
        for aid, albums in artist_master_albums_map.items()
    }


# ---------------------------------------------------------------------------
# Step 1 — Parse releases dump
# ---------------------------------------------------------------------------
def parse_releases(
    releases_path: str,
) -> tuple[set[int], dict[int, set[str]]]:
    """
    Stream-parse releases.xml.

    Returns:
        metal_artist_ids : set[int]             — artist IDs with >=1 metal release
        artist_styles    : dict[int, set[str]]  — artist ID -> accumulated styles
    """
    metal_artist_ids: set[int] = set()
    artist_styles: dict[int, set[str]] = {}

    print(f"[Step 1] Parsing releases: {releases_path}")

    release_count = 0
    context = ET.iterparse(open_xml(releases_path), events=("end",))

    try:
        for _event, elem in context:
            if elem.tag != "release":
                continue  # do NOT clear — child elements must survive until <release> ends

            release_count += 1
            if release_count % PROGRESS_INTERVAL == 0:
                print(f"  ... {release_count:,} releases processed")

            release_styles: list[str] = []
            for style_elem in elem.findall("./styles/style"):
                if style_elem.text:
                    release_styles.append(style_elem.text.strip())

            if not any(is_metal_style(s) for s in release_styles):
                elem.clear()
                continue

            for artist_node in elem.findall("./artists/artist"):
                id_elem = artist_node.find("id")
                if id_elem is None or not id_elem.text:
                    continue
                try:
                    aid = int(id_elem.text.strip())
                except ValueError:
                    continue

                metal_artist_ids.add(aid)
                if aid not in artist_styles:
                    artist_styles[aid] = set()
                for s in release_styles:
                    artist_styles[aid].add(s)

            elem.clear()

    except (ET.ParseError, gzip.BadGzipFile, EOFError, OSError) as exc:
        print(
            f"  [Step 1] Warning: gzip stream ended early after "
            f"{release_count:,} releases ({type(exc).__name__}: {exc}). "
            "Continuing with data collected so far."
        )

    print(
        f"[Step 1] Done. {release_count:,} releases parsed. "
        f"{len(metal_artist_ids):,} metal-relevant artist IDs collected."
    )
    return metal_artist_ids, artist_styles

# ---------------------------------------------------------------------------
# Step 2 â€” Load all shards into memory
# ---------------------------------------------------------------------------
def load_shards(shards_dir: str) -> tuple[dict[str, list[dict]], set[str]]:
    """
    Read every base-{xx}.json from shards_dir.

    Returns:
        shards          : dict[str, list[dict]]  â€” shard key â†’ entries
        modified_shards : set[str]               â€” initially empty
    """
    shards: dict[str, list[dict]] = {}
    print(f"[Step 2] Loading shards from {shards_dir} â€¦")

    for filename in os.listdir(shards_dir):
        if not (filename.startswith("base-") and filename.endswith(".json")):
            continue
        key = filename[len("base-"):-len(".json")]
        filepath = os.path.join(shards_dir, filename)
        with open(filepath, "r", encoding="utf-8") as fh:
            shards[key] = json.load(fh)

    print(f"[Step 2] Loaded {len(shards)} shards.")
    modified_shards: set[str] = set()
    return shards, modified_shards


# ---------------------------------------------------------------------------
# MusicBrainz URL extraction helper
# ---------------------------------------------------------------------------
_MB_PATTERN = re.compile(
    r"musicbrainz\.org/artist/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)


def extract_mbid_from_urls(urls: list[str]) -> str | None:
    for url in urls:
        m = _MB_PATTERN.search(url)
        if m:
            return m.group(1).lower()
    return None


# ---------------------------------------------------------------------------
# Step 3 + 4 â€” Stream-parse artists dump, enrich shards, write stubs
# ---------------------------------------------------------------------------
def parse_artists_and_enrich(
    artists_path: str,
    metal_artist_ids: set[int],
    artist_styles: dict[int, set[str]],
    artist_albums: dict[int, list[dict]],
    shards: dict[str, list[dict]],
    modified_shards: set[str],
) -> dict:
    """
    Stream-parse artists.xml. For each metal artist, enrich shards and write
    stub entries for aliases/nameVariations.

    Returns a stats+warnings dict for the final report.
    """
    stats = {
        "discogsArtistsParsed": 0,
        "metalRelevantArtists": 0,
        "matchedExistingEntries": 0,
        "newMetalCatalogEntries": 0,
        "stubsWritten": 0,
        "shardsModified": 0,  # filled later
        "mbidConflictsSkipped": 0,
        "multiMatchSkipped": 0,
        "albumsAddedToExistingEntries": 0,
    }
    warnings: list[dict] = []

    print(f"[Step 3] Parsing artists: {artists_path}")

    context = ET.iterparse(open_xml(artists_path), events=("end",))

    try:
        for _event, elem in context:
            if elem.tag != "artist":
                continue  # do NOT clear â€” child elements must survive until <artist> ends

            stats["discogsArtistsParsed"] += 1

            # --- Extract artist fields ---
            id_elem = elem.find("id")
            if id_elem is None or not id_elem.text:
                elem.clear()
                continue

            try:
                aid = int(id_elem.text.strip())
            except ValueError:
                elem.clear()
                continue

            if aid not in metal_artist_ids:
                elem.clear()
                continue


            stats["metalRelevantArtists"] += 1

            # Name (strip Discogs disambiguation suffix e.g. "Death (2)")
            name_elem = elem.find("name")
            raw_name: str = (name_elem.text or "").strip() if name_elem is not None else ""
            display_name = re.sub(r"\s*\(\d+\)$", "", raw_name).strip()

            # Name variations
            name_variations: list[str] = []
            for nv in elem.findall("./namevariations/name"):
                if nv.text and nv.text.strip():
                    name_variations.append(nv.text.strip())

            # Aliases
            aliases: list[str] = []
            for al in elem.findall("./aliases/name"):
                if al.text and al.text.strip():
                    aliases.append(al.text.strip())

            # URLs
            urls: list[str] = []
            for url_elem in elem.findall("./urls/url"):
                if url_elem.text and url_elem.text.strip():
                    urls.append(url_elem.text.strip())

            # Profile
            profile_elem = elem.find("profile")
            raw_profile: str = (profile_elem.text or "").strip() if profile_elem is not None else ""
            profile: str | None = truncate_profile(raw_profile) if raw_profile else None

            # MusicBrainz ID from URLs
            discogs_mbid: str | None = extract_mbid_from_urls(urls)

            # Styles for this artist (normalised)
            raw_styles: set[str] = artist_styles.get(aid, set())
            computed_styles: list[str] = normalise_styles(raw_styles)
            computed_albums: list[dict] = artist_albums.get(aid, [])

            # --- Locate shard for canonical name ---
            norm_name = normalize(display_name)
            if not norm_name:
                elem.clear()
                continue

            sk = shard_key(norm_name)

            # Ensure the shard bucket exists in memory
            if sk not in shards:
                shards[sk] = []

            shard_entries: list[dict] = shards[sk]

            # Find matching entries by normalizedName
            matches = [e for e in shard_entries if e.get("normalizedName") == norm_name]

            # ----------------------------------------------------------------
            # Match & merge logic
            # ----------------------------------------------------------------
            if len(matches) == 0:
                # --- Insert new metal-catalog entry ---
                new_entry: dict = {
                    "normalizedName": norm_name,
                    "displayName": display_name,
                    "mbid": discogs_mbid,
                    "albums": computed_albums,
                    "tags": [],
                    "origin": None,
                    "discogsArtistId": aid,
                    "styles": computed_styles,
                    "aliases": aliases if aliases else None,
                    "nameVariations": name_variations if name_variations else None,
                    "profile": profile,
                    "source": "metal-catalog",
                }
                shard_entries.append(new_entry)
                modified_shards.add(sk)
                stats["newMetalCatalogEntries"] += 1

            elif len(matches) == 1:
                # --- Enrich in-place (STRICT NO OVERRIDE) ---
                entry = matches[0]

                # mbid handling (fill if missing)
                if discogs_mbid and not entry.get("mbid"):
                    entry["mbid"] = discogs_mbid

                # Fill missing fields
                if "discogsArtistId" not in entry:
                    entry["discogsArtistId"] = aid
                if not entry.get("styles"):
                    entry["styles"] = computed_styles
                
                merged_albums, added_albums = merge_album_records(
                    entry.get("albums"),
                    computed_albums,
                )
                entry["albums"] = merged_albums
                stats["albumsAddedToExistingEntries"] += added_albums
                
                if not entry.get("aliases"):
                    entry["aliases"] = aliases
                if not entry.get("nameVariations"):
                    entry["nameVariations"] = name_variations
                if profile and not entry.get("profile"):
                    entry["profile"] = profile

                modified_shards.add(sk)
                stats["matchedExistingEntries"] += 1

            else:
                # Multiple matches â€” need mbid to disambiguate
                if not discogs_mbid:
                    warnings.append({
                        "type": "multi_match",
                        "normalizedName": norm_name,
                        "detail": (
                            f"Discogs artist {aid} has no mbid; "
                            f"{len(matches)} shard entries found â€” skipping."
                        ),
                    })
                    stats["multiMatchSkipped"] += 1
                    elem.clear()
                    continue

                # Try to find the shard entry whose mbid matches Discogs mbid
                mbid_matches = [e for e in matches if e.get("mbid") == discogs_mbid]

                if len(mbid_matches) != 1:
                    warnings.append({
                        "type": "multi_match",
                        "normalizedName": norm_name,
                        "detail": (
                            f"Discogs artist {aid} mbid={discogs_mbid}; "
                            f"{len(mbid_matches)} shard entries with that mbid â€” skipping."
                        ),
                    })
                    stats["multiMatchSkipped"] += 1
                    elem.clear()
                    continue

                entry = mbid_matches[0]
                
                # STRICT NO OVERRIDE
                if "discogsArtistId" not in entry:
                    entry["discogsArtistId"] = aid
                if not entry.get("styles"):
                    entry["styles"] = computed_styles
                
                merged_albums, added_albums = merge_album_records(
                    entry.get("albums"),
                    computed_albums,
                )
                entry["albums"] = merged_albums
                stats["albumsAddedToExistingEntries"] += added_albums
                
                if not entry.get("aliases"):
                    entry["aliases"] = aliases
                if not entry.get("nameVariations"):
                    entry["nameVariations"] = name_variations
                if profile and not entry.get("profile"):
                    entry["profile"] = profile

                modified_shards.add(sk)
                stats["matchedExistingEntries"] += 1

            # ----------------------------------------------------------------
            # Step 4 â€” Write stub entries for aliases and nameVariations
            # ----------------------------------------------------------------
            all_alt_names: list[str] = []
            for n in aliases:
                all_alt_names.append(n)
            for n in name_variations:
                all_alt_names.append(n)

            for alt_name in all_alt_names:
                alt_norm = normalize(alt_name)
                if not alt_norm:
                    continue

                alt_sk = shard_key(alt_norm)

                # Ensure bucket exists
                if alt_sk not in shards:
                    shards[alt_sk] = []

                alt_bucket: list[dict] = shards[alt_sk]

                # Do not write a stub if any entry already has this normalizedName
                already_present = any(
                    e.get("normalizedName") == alt_norm for e in alt_bucket
                )
                if already_present:
                    continue

                stub: dict = {
                    "normalizedName": alt_norm,
                    "displayName": alt_name,
                    "discogsArtistId": aid,
                    "canonicalNormalizedName": norm_name,
                    "stub": True,
                }
                alt_bucket.append(stub)
                modified_shards.add(alt_sk)
                stats["stubsWritten"] += 1

            elem.clear()

    except (ET.ParseError, gzip.BadGzipFile, EOFError, OSError) as exc:
        print(
            f"  [Step 3] Warning: gzip stream ended early after "
            f"{stats['discogsArtistsParsed']:,} artists "
            f"({type(exc).__name__}: {exc}). "
            "Continuing with data collected so far."
        )

    print(
        f"[Step 3/4] Done. "
        f"{stats['discogsArtistsParsed']:,} artists parsed, "
        f"{stats['metalRelevantArtists']:,} metal-relevant."
    )
    return {"stats": stats, "warnings": warnings}


# ---------------------------------------------------------------------------
# Step 5 â€” Write back modified shards (atomic)
# ---------------------------------------------------------------------------
def write_modified_shards(
    shards: dict[str, list[dict]],
    modified_shards: set[str],
    shards_dir: str,
) -> None:
    print(f"[Step 5] Writing {len(modified_shards)} modified shards â€¦")

    for sk in modified_shards:
        filename = f"base-{sk}.json"
        dest_path = os.path.join(shards_dir, filename)
        tmp_path  = dest_path + ".tmp"

        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(shards[sk], fh, ensure_ascii=False)

        os.replace(tmp_path, dest_path)

    print(f"[Step 5] Done.")


# ---------------------------------------------------------------------------
# Step 5b â€” Update cache-manifest.json (builtAt only)
# ---------------------------------------------------------------------------
def update_manifest(manifest_path: str, built_at_ms: int) -> None:
    with open(manifest_path, "r", encoding="utf-8") as fh:
        manifest = json.load(fh)

    manifest["builtAt"] = built_at_ms

    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)

    os.replace(tmp_path, manifest_path)
    print(f"[Step 5] Manifest updated: builtAt={built_at_ms}")


# ---------------------------------------------------------------------------
# Step 6 â€” Write enrichment report
# ---------------------------------------------------------------------------
def write_report(
    report_path: str,
    built_at_ms: int,
    stats: dict,
    warnings: list[dict],
) -> None:
    report = {
        "builtAt": built_at_ms,
        "stats": stats,
        "warnings": warnings,
    }

    tmp_path = report_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    os.replace(tmp_path, report_path)
    print(f"[Step 6] Enrichment report written: {report_path}")


# ---------------------------------------------------------------------------
# Console summary
# ---------------------------------------------------------------------------
def print_summary(stats: dict) -> None:
    print(
        "\nDone.\n"
        f"  Discogs artists parsed:   {stats['discogsArtistsParsed']:>12,}\n"
        f"  Metal-relevant artists:   {stats['metalRelevantArtists']:>12,}\n"
        f"  Matched existing entries: {stats['matchedExistingEntries']:>12,}\n"
        f"  New metal-catalog entries:{stats['newMetalCatalogEntries']:>12,}\n"
        f"  Stubs written:            {stats['stubsWritten']:>12,}\n"
        f"  Shards modified:          {stats['shardsModified']:>12,}\n"
        f"  Albums backfilled:        {stats['albumsAddedToExistingEntries']:>12,}\n"
        f"  mbid conflicts skipped:   {stats['mbidConflictsSkipped']:>12,}\n"
        f"  Multi-match skipped:      {stats['multiMatchSkipped']:>12,}"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    built_at_ms = int(time.time() * 1000)

    # ------------------------------------------------------------------
    # Step 0 â€” Build canonical artist albums from masters dump
    # ------------------------------------------------------------------
    artist_master_albums = parse_masters(DISCOGS_MASTERS_DUMP_PATH)

    # ------------------------------------------------------------------
    # Step 1 â€” Build metal artist ID set and styles map from releases dump
    # ------------------------------------------------------------------
    metal_artist_ids, artist_styles = parse_releases(DISCOGS_RELEASES_DUMP_PATH)

    # ------------------------------------------------------------------
    # Step 2 â€” Load all existing shards into memory
    # ------------------------------------------------------------------
    shards, modified_shards = load_shards(SHARDS_DIR)

    # ------------------------------------------------------------------
    # Steps 3 & 4 â€” Enrich from artists dump + write stubs
    # ------------------------------------------------------------------
    result = parse_artists_and_enrich(
        DISCOGS_ARTISTS_DUMP_PATH,
        metal_artist_ids,
        artist_styles,
        artist_master_albums,
        shards,
        modified_shards,
    )

    stats    = result["stats"]
    warnings = result["warnings"]

    # Update shards modified count now that we know the final set
    stats["shardsModified"] = len(modified_shards)

    # ------------------------------------------------------------------
    # Step 5 â€” Write back modified shards (atomic) + update manifest
    # ------------------------------------------------------------------
    write_modified_shards(shards, modified_shards, SHARDS_DIR)
    update_manifest(MANIFEST_PATH, built_at_ms)

    # ------------------------------------------------------------------
    # Step 6 â€” Write enrichment report
    # ------------------------------------------------------------------
    write_report(REPORT_PATH, built_at_ms, stats, warnings)

    # ------------------------------------------------------------------
    # Final summary
    # ------------------------------------------------------------------
    print_summary(stats)


if __name__ == "__main__":
    main()
