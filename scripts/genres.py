"""
Genre normalisation for the metadata shards.

Goal: every genre string keeps its real meaning and is spelled one way.
Non-metal genres (rock, folk, punk, electronic, ...) are kept as-is apart
from spelling; only junk values (URLs, chart names, moods) are dropped.

Passes, per artist:
  1. clean_genre()      - split compound strings, fix spelling/casing, drop junk
  2. resolve_context()  - decide ambiguous tags using the artist's other genres:
       * truncated Metal Archives names ("Us Power", "Symphonic Gothic") get
         their "Metal" suffix back when the artist is a metal act
       * "X Metal" labels produced by the old shorthand map from a bare Discogs
         style ("Folk" -> "Folk Metal", "Ambient" -> "Ambient Metal") go back
         to the bare style when the artist has no other metal genre
  3. dedupe, and drop the generic "Metal" when a specific subgenre is present
"""

import re

# ---------------------------------------------------------------------------
# Junk: not genres at all
# ---------------------------------------------------------------------------
_JUNK = {
    "top metal albums of the decade 2020 - 2029", "best of 2021", "best of 2022",
    "to get", "debut", "5+ wochen", "offizielle charts", "vyrzukhisuc-album",
    "rca records", "melanie thornton", "desolation boulevard", "dead hensons",
    "muppet", "the muppets", "muppets", "00s", "90s", "80 s", "oldies 70 s",
    "live", "cover", "double album", "posthumous", "alright", "escape", "fun",
    "cute", "nocturnal", "sensual", "manic", "ukraine", "lugansk", "english",
    "dutch", "swedish", "canadian", "female vocalists", "metal queen",
    "various", "r", "www.discogs.com", "electronic elements", "sludge elements",
    "classical influences", "modern", "big music", "pixel art", "90 s rap",
    "non-music",
}
_JUNK_RE = re.compile(r"^https?:|discogs\.com|^\d+$")

# ---------------------------------------------------------------------------
# Whole-string aliases (lower-case key -> canonical)
# ---------------------------------------------------------------------------
_ALIASES = {
    # rock / pop / misc spelling variants
    "prog rock": "Progressive Rock",
    "goth rock": "Gothic Rock",
    "blues-rock": "Blues Rock",
    "pop/rock": "Pop Rock",
    "rock/pop": "Pop Rock",
    "rock pop": "Pop Rock",
    "contemporary pop/rock": "Pop Rock",
    "alternative pop/rock": "Alternative Rock",
    "adult alternative pop/rock": "Adult Alternative Rock",
    "alternative/indie rock": "Alternative Rock",
    "alternrock": "Alternative Rock",
    "alt rock": "Alternative Rock",
    "exchange e252698833 alternative": "Alternative",
    "rock music": "Rock",
    "rock and indie": "Rock",
    "rock and roll": "Rock & Roll",
    "rhythm and blues": "Rhythm & Blues",
    "contemporary r&b": "Contemporary R&B",
    "rnb": "R&B",
    "indonesian r&b": "Indonesian R&B",
    "hip-hop": "Hip Hop",
    "hip hop/rap": "Hip Hop",
    "hip-hop/rap": "Hip Hop",
    "rap/hip hop": "Hip Hop",
    "pop-rap": "Pop Rap",
    "synthpop": "Synth-pop",
    "synth pop": "Synth-pop",
    "trip-hop": "Trip Hop",
    "psy-trance": "Psytrance",
    "dark wave": "Darkwave",
    "cold wave": "Coldwave",
    "electro industrial": "Electro-Industrial",
    "electro-industrial": "Electro-Industrial",
    "drum n bass": "Drum & Bass",
    "drum and bass": "Drum & Bass",
    "death rock": "Deathrock",
    "neo folk": "Neofolk",
    "neo-classical": "Neoclassical",
    "neo classical": "Neoclassical",
    "j rock": "J-Rock",
    "j-rock": "J-Rock",
    "j-pop": "J-Pop",
    "k-pop": "K-Pop",
    "k-rock": "K-Rock",
    "italodance": "Italo Dance",
    "bossanova": "Bossa Nova",
    "new-age": "New Age",
    "children's music": "Children's",
    "children": "Children's",
    "kids": "Children's",
    "classical music": "Classical",
    "western classical music": "Classical",
    "swing music": "Swing",
    "rock ballads": "Rock Ballad",
    "club/dance": "Dance",
    "jam bands": "Jam Band",
    "ebm": "EBM",
    "edm": "EDM",
    "idm": "IDM",
    "aor": "AOR",
    "cedm": "CEDM",
    "mpb": "MPB",
    "ccm": "CCM",
    "uk garage": "UK Garage",
    "japanese vgm": "Video Game Music",
    "p.funk": "P-Funk",
    "psychadelic": "Psychedelic",
    "pyschedelic folk": "Psychedelic Folk",
    "muzika mizrahit": "Mizrahi",
    "oi": "Oi!",
    "power violence": "Powerviolence",
    "crust": "Crust Punk",
    "neo prog prog metal": "Progressive Metal",

    # metal naming
    "gothenburg": "Melodic Death Metal",
    "modern gothenburg": "Melodic Death Metal",
    "new wave of british heavy": "NWOBHM",
    "nwobhm heavy metal": "NWOBHM",
    "first wave of black": "First Wave Black Metal",
    "goth metal": "Gothic Metal",
    "trash metal": "Thrash Metal",
    "epic trash metal": "Epic Thrash Metal",
    "doom death": "Death Doom Metal",
    "doom death metal": "Death Doom Metal",
    "industrial/doom-metal": "Industrial Doom Metal",
    "heavy-speed metal": "Heavy Speed Metal",
    "heavy-speed-thrash metal": "Heavy Speed Thrash Metal",
    "black 'n roll": "Black 'n' Roll",
    "black n' roll": "Black 'n' Roll",
    "metal opera": "Opera Metal",
    "operatic metal": "Opera Metal",
    "nu-metric metal": "Nu Metal",
}

# Token-level spelling fixes applied inside longer names
_TOKEN_FIXES = [
    (re.compile(r"\bavant[- ]?garde\b", re.I), "Avant-garde"),
    (re.compile(r"\bpost[- ]black\b", re.I), "Post-Black"),
    (re.compile(r"\bpost[- ]metal\b", re.I), "Post-Metal"),
    (re.compile(r"\bpost[- ]rock\b", re.I), "Post-Rock"),
    (re.compile(r"\bpost[- ]hardcore\b", re.I), "Post-Hardcore"),
    (re.compile(r"\bpost[- ]punk\b", re.I), "Post-Punk"),
    (re.compile(r"\bpost-grunge\b", re.I), "Post-Grunge"),
    (re.compile(r"\bnu[- ]metal\b", re.I), "Nu Metal"),
    (re.compile(r"\bdeath-doom\b", re.I), "Death Doom"),
    (re.compile(r"\b(death|black|thrash) 'n' roll\b", re.I),
     lambda m: m.group(1).title() + " 'n' Roll"),
    (re.compile(r"^us (heavy|power)\b", re.I), lambda m: "US " + m.group(1).title()),
    (re.compile(r"\bnwobhm\b", re.I), "NWOBHM"),
    (re.compile(r"\brac\b", re.I), "RAC"),
    (re.compile(r"\bmetal\b"), "Metal"),
]

# ---------------------------------------------------------------------------
# Metal classification
# ---------------------------------------------------------------------------
# Labels the old shorthand map invented from a bare Discogs style. The bare
# style is what the source said, so these always revert.
_FALSE_METAL = {
    "hardcore metal": "Hardcore",
    "ambient metal": "Ambient",
    "noise metal": "Noise",
    "electro metal": "Electro",
    "electronic metal": "Electronic",
    "country metal": "Country",
    "crust metal": "Crust Punk",
}

# Real metal genres that the shorthand map also produced from non-metal
# Discogs styles. Reverted only for artists that are evidently not metal acts
# (no metal genre, and at least one other non-metal genre).
_AMBIGUOUS_METAL = {
    "folk metal": "Folk",
    "industrial metal": "Industrial",
    "experimental metal": "Experimental",
    "drone metal": "Drone",
    "avant-garde metal": "Avant-garde",
    "celtic metal": "Celtic",
    "medieval metal": "Medieval",
    "neoclassical metal": "Neoclassical",
    "psychedelic metal": "Psychedelic",
    "southern metal": "Southern Rock",
}

# Last words that, when missing "Metal", always mean a metal subgenre
# (Metal Archives names with the suffix chopped off: "Us Power", "Melodic Heavy")
_ALWAYS_METAL_TAIL = {"power", "heavy", "black", "death", "thrash", "doom",
                      "speed", "sludge", "groove", "stoner"}
_NOT_TRUNCATED = {"crossover thrash", "dissonant death", "death", "black",
                  "doom", "heavy", "power", "groove"}

# Last words that only mean metal when the artist is a metal act
# ("Pagan Folk", "Symphonic Gothic", "Progressive Math")
_CONTEXT_METAL_TAIL = {"folk", "gothic", "progressive", "symphonic", "math",
                       "industrial", "avant-garde", "atmospheric", "melodic",
                       "epic", "neoclassical", "nu"}
# "... Folk" is real folk music ("Acoustic Folk", "Celtic Folk") unless the
# modifier is itself a metal one
_METAL_FOLK_PREFIX = {"pagan", "viking", "oriental", "blackened", "black",
                      "death", "symphonic", "epic", "atmospheric", "melodic",
                      "extreme", "power", "speed", "thrash", "doom"}

# Metal genres whose names do not contain the word "Metal"
_METAL_WITHOUT_WORD = re.compile(
    r"\b(metal|death|grind|math|noise|trance)core\b|grind|djent|blackgaze|"
    r"'n' roll|nwobhm|gorenoise|thall|doomgaze|metalgaze", re.I)


def _is_core_metal(genre: str) -> bool:
    """Unambiguous metal genre (used to decide if an artist is a metal act)."""
    g = genre.lower()
    if g in _FALSE_METAL or g in _AMBIGUOUS_METAL or g == "metal":
        return False
    if "metal" in g:
        return True
    return bool(_METAL_WITHOUT_WORD.search(g)) and "punk" not in g


def _title(word: str) -> str:
    return word if any(c.isupper() for c in word[1:]) else word[:1].upper() + word[1:]


def clean_genre(raw: str) -> list[str]:
    """Clean one raw genre string. May return 0..n genres."""
    out = []
    parts = re.split(r"\s*[|;]\s*", raw)
    if len(parts) == 1 and re.fullmatch(r"[^/]+ rock / [^/]+ rock", raw, re.I):
        parts = raw.split(" / ")
    for part in parts:
        g = re.sub(r"\s+with .+ (influences|elements)$", "", part, flags=re.I)
        g = re.sub(r"\s+(electronic|folk) elements$", "", g, flags=re.I)
        g = re.sub(r"\s+", " ", g).strip(" ,")
        if not g:
            continue
        key = g.lower()
        if key in _JUNK or _JUNK_RE.search(key):
            continue
        if key in _ALIASES:
            out.append(_ALIASES[key])
            continue
        # collapse doubled words: "Death Death Metal", "Grindcore Grindcore"
        words = g.split(" ")
        dedup = [w for i, w in enumerate(words) if i == 0 or w.lower() != words[i - 1].lower()]
        g = " ".join(_title(w) for w in dedup)
        for pat, rep in _TOKEN_FIXES:
            g = pat.sub(rep, g)
        g = _ALIASES.get(g.lower(), g)
        out.append(g)
    return out


def _resolve(genre: str, metal_act: bool, non_metal_act: bool = False) -> str:
    g = genre.lower()
    if g in _FALSE_METAL:
        return _FALSE_METAL[g]
    if g in _AMBIGUOUS_METAL and non_metal_act:
        return _AMBIGUOUS_METAL[g]
    words = g.split(" ")
    if len(words) > 1 and "metal" not in words and g not in _NOT_TRUNCATED:
        tail = words[-1].split("-")[-1]
        if tail in _ALWAYS_METAL_TAIL:
            return f"{genre} Metal"
        if tail == "folk" and words[0] not in _METAL_FOLK_PREFIX:
            return genre
        if tail in _CONTEXT_METAL_TAIL and metal_act:
            return f"{genre} Metal"
    return genre


def _finish(genres: list[str]) -> list[str]:
    seen, out = set(), []
    for g in genres:
        if g.lower() not in seen:
            seen.add(g.lower())
            out.append(g)
    if len(out) > 1 and any("metal" in g.lower() and g.lower() != "metal" for g in out):
        out = [g for g in out if g.lower() != "metal"]
    return out


def is_metal_act(genres) -> bool:
    return any(_is_core_metal(g) for g in genres)


def normalise_artist(artist: dict) -> None:
    """Normalise album genres and tags of one shard artist entry in place."""
    albums = artist.get("albums", [])
    cleaned_albums = [[c for g in al.get("genres", []) for c in clean_genre(g)] for al in albums]
    cleaned_tags = [c for g in artist.get("tags", []) for c in clean_genre(g)]

    every = [g for gs in cleaned_albums for g in gs] + cleaned_tags
    # metal_act=False here so context-dependent names can't vouch for themselves
    resolved = [_resolve(g, False) for g in every]
    metal_act = is_metal_act(resolved)
    non_metal_act = not metal_act and any(
        g.lower() not in _AMBIGUOUS_METAL and g.lower() != "metal" for g in resolved)

    for al, gs in zip(albums, cleaned_albums):
        if "genres" in al:
            al["genres"] = _finish([_resolve(g, metal_act, non_metal_act) for g in gs])
    if "tags" in artist:
        artist["tags"] = _finish([_resolve(g, metal_act, non_metal_act) for g in cleaned_tags])
