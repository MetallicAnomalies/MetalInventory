"""
Metal Archives Upcoming Releases Scraper
========================================
Fetches upcoming metal releases from the Metal Archives AJAX endpoint
and merges them into the existing metal_releases.json file.

Modes:
  pipeline (default) – today -7 days → +60 days.  Used in CI; avoids
                       hammering the site for older historical data.
  backfill           – today -14 days → +60 days.  Run locally once to
                       seed the JSON with recently-released albums.

Robustness:
  - Transient failures (timeouts, connection errors, 429, 5xx, non-JSON
    challenge pages) are retried with exponential backoff.
  - If a page still fails, the pages fetched so far are merged (merging is
    additive) and the script exits 1 so CI / npm see the failure.
  - A corrupt metal_releases.json is never treated as empty, and writes are
    atomic (temp file + rename), so a crash can't truncate the file.

Usage:
  python metal_archives.py                  # pipeline mode
  python metal_archives.py --mode backfill  # local back-fill
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MA_AJAX_URL = (
    "https://www.metal-archives.com/release/ajax-upcoming/json/1"
)

MA_HEADERS = {
    # "Scrobex" is whitelisted by the Metal Archives admin (HellBlazer) to bypass Cloudflare
    "User-Agent": "Scrobex",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.metal-archives.com/",
}

# Anchored to the repo root so the script works from any working directory.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(REPO_ROOT, "metal_releases.json")

# Pagination: MA returns at most 100 rows per request
PAGE_SIZE = 100
MAX_PAGES = 200                # hard stop against a runaway pagination loop
PAGE_DELAY = 5                 # polite delay between pages (seconds)

# Retry policy for transient failures
MAX_ATTEMPTS = 4
RETRY_BASE_DELAY = 10          # seconds; doubles each attempt
REQUEST_TIMEOUT = (10, 45)     # (connect, read) seconds

# Refuse to write if the merged file would lose more than half its entries
MIN_KEEP_RATIO = 0.5


class FetchError(Exception):
    """A non-recoverable error while fetching from Metal Archives."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_console() -> None:
    """Avoid UnicodeEncodeError on non-UTF-8 consoles (e.g. Windows cp1252 pipes)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def atomic_write_json(path: str, data, indent: int = 4) -> None:
    """Write JSON to a temp file in the same directory, then atomically replace."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# Matches a trailing parenthetical suffix, e.g. " (Heresies Against the Flow of Time)"
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def _normalize_album_key(title: str) -> str:
    """
    Return a canonical dedup key for an album title:
      - strip trailing parenthetical suffixes like "(Subtitle)" or "(Reissue)"
      - fold to lowercase, strip leading/trailing whitespace

    This ensures that HeavyMusicHQ's "The Violence Of Time" and Metal
    Archives' "THE VIOLENCE OF TIME (Heresies Against the Flow of Time)"
    resolve to the same key and are merged into one entry.
    """
    key = _TRAILING_PAREN_RE.sub("", title).strip().lower()
    return key or title.strip().lower()


def _strip_html(raw) -> str:
    """Remove HTML tags from a Metal Archives cell value."""
    if not isinstance(raw, str):
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(strip=True)


def _extract_ma_link(raw) -> str:
    """Pull the href out of an anchor tag, or return empty string."""
    if not isinstance(raw, str):
        return ""
    soup = BeautifulSoup(raw, "html.parser")
    tag = soup.find("a")
    return tag["href"] if tag and tag.get("href") else ""


_GENRE_NOUNS = {
    "metal", "core", "grind", "grindcore", "punk", "rock", "djent", "metalcore",
    "deathcore", "hardcore", "crust", "noise", "ambient", "industrial", "doom",
}


def _parse_genres(raw: str) -> list[str]:
    """
    Split a Metal Archives genre string into individual genres.

    MA uses both '/' and ';' as separators, and sometimes appends
    time qualifiers like '(early)' or '(later)' that are discarded.

    Examples:
      "Gothic/Doom Metal"                              -> ["Gothic Metal", "Doom Metal"]
      "Stoner/Doom Metal"                              -> ["Stoner Metal", "Doom Metal"]
      "Metalcore (early); Prog/Melodic Black Metal"    -> ["Metalcore", "Prog Metal", "Melodic Black Metal"]
    """
    # Strip time qualifiers like (early), (later), (early-mid), etc.
    cleaned = re.sub(r'\(\s*[^)]*\)', '', raw)

    # Split on ; first (phrase-level separator)
    phrases = [p.strip() for p in cleaned.split(';') if p.strip()]

    genres: list[str] = []
    for phrase in phrases:
        # Split on / within each phrase
        parts = [p.strip() for p in phrase.split('/') if p.strip()]

        if len(parts) == 1:
            genres.append(parts[0])
        else:
            # A bare-adjective part borrows the head noun (last word) of the
            # next part: "Gothic/Doom Metal" -> "Gothic Metal", "Doom Metal";
            # "Black/Death Metal/Grindcore" -> "Black Metal", "Death Metal", "Grindcore".
            for i, part in enumerate(parts):
                words = part.split()
                if i == len(parts) - 1 or words[-1].lower() in _GENRE_NOUNS:
                    genres.append(part)
                    continue
                noun = next((parts[j].split()[-1] for j in range(i + 1, len(parts))
                             if parts[j].split()[-1].lower() in _GENRE_NOUNS),
                            parts[i + 1].split()[-1])
                genres.append(f"{part} {noun}")

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for g in genres:
        key = g.lower()
        if key not in seen:
            seen.add(key)
            unique.append(g)

    return unique


def _build_date_range(mode: str = "pipeline") -> tuple[str, str]:
    """
    Returns (from_date, to_date) as 'YYYY-MM-DD' strings.

    pipeline  – today -7 days → +60 days (CI-safe)
    backfill  – today -14 days → +60 days (local seeding run)
    """
    today = datetime.now(timezone.utc)
    back = 14 if mode == "backfill" else 7
    from_date = (today - timedelta(days=back)).strftime("%Y-%m-%d")
    to_date = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    return from_date, to_date


def _snippet(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + f"... [{len(text) - limit} more chars]"


def _wait_before_retry(attempt: int, reason: str, retry_after: Optional[str] = None) -> None:
    if attempt >= MAX_ATTEMPTS:
        return
    delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
    if retry_after and retry_after.strip().isdigit():
        delay = max(delay, min(int(retry_after), 300))
    print(f"\n[MetalArchives] {reason} - retry {attempt}/{MAX_ATTEMPTS - 1} in {delay}s")
    time.sleep(delay)


def _fetch_page(session: requests.Session, from_date: str, to_date: str,
                offset: int, echo: int) -> dict:
    """Fetch a single page from the Metal Archives AJAX endpoint, with retries."""
    params = {
        "sEcho":          echo,
        "iColumns":       6,
        "sColumns":       "",
        "iDisplayStart":  offset,
        "iDisplayLength": PAGE_SIZE,
        "mDataProp_0":    0,
        "mDataProp_1":    1,
        "mDataProp_2":    2,
        "mDataProp_3":    3,
        "mDataProp_4":    4,
        "mDataProp_5":    5,
        "iSortCol_0":     4,          # sort by release date
        "sSortDir_0":     "asc",
        "iSortingCols":   1,
        "bSortable_0":    "true",
        "bSortable_1":    "true",
        "bSortable_2":    "true",
        "bSortable_3":    "true",
        "bSortable_4":    "true",
        "bSortable_5":    "true",
        "includeVersions": 0,
        "fromDate":       from_date,
        "toDate":         to_date,
        "_":              int(datetime.now(timezone.utc).timestamp() * 1000),
    }

    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(MA_AJAX_URL, headers=MA_HEADERS, params=params,
                               timeout=REQUEST_TIMEOUT)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = f"network error: {exc}"
            _wait_before_retry(attempt, last_error)
            continue

        status = resp.status_code
        if status == 403 and "cf-mitigated" in resp.headers:
            # Cloudflare challenge: datacenter IP blocked, retrying won't help.
            raise FetchError(
                "Cloudflare bot-fight-mode challenge detected (403). Requests must "
                "originate from a residential IP (self-hosted runner)."
            )
        if status == 429 or status >= 500:
            last_error = f"HTTP {status}"
            _wait_before_retry(attempt, last_error, resp.headers.get("Retry-After"))
            continue
        if status >= 400:
            raise FetchError(f"HTTP {status} for {resp.url}\n{_snippet(resp.text)}")

        try:
            data = resp.json()
        except ValueError:
            # Usually an HTML challenge / maintenance page served with 200.
            last_error = f"non-JSON response: {_snippet(resp.text, 200)}"
            _wait_before_retry(attempt, "non-JSON response")
            continue

        if not isinstance(data, dict) or not isinstance(data.get("aaData"), list):
            raise FetchError(f"Unexpected payload shape: {_snippet(str(data))}")
        return data

    raise FetchError(f"giving up after {MAX_ATTEMPTS} attempts ({last_error})")


def _parse_rows(rows: list) -> list[dict]:
    """
    Convert raw MA row data into normalised release dicts.

    MA column layout (6 columns, verified against live API):
      0 – Artist      (HTML anchor)
      1 – Album       (HTML anchor)
      2 – Release type (text, e.g. "Full-length", "EP")
      3 – Genre       (text)
      4 – Release date (e.g. "May 8th, 2026")
      5 – Added-on    (timestamp string, e.g. "2026-01-16 04:45:43") – NOT a label
    """
    results = []
    skipped_dates = 0
    for row in rows:
        try:
            artist      = _strip_html(row[0])
            album       = _strip_html(row[1])
            rel_type    = _strip_html(row[2])
            genre       = _strip_html(row[3])
            date_raw    = _strip_html(row[4])
            added_on    = row[5].strip() if len(row) > 5 and isinstance(row[5], str) else ""
            artist_url  = _extract_ma_link(row[0])
            album_url   = _extract_ma_link(row[1])
        except (IndexError, KeyError, TypeError):
            continue

        if not artist or not album:
            continue

        # Parse date like "May 8th, 2026"
        clean_date = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_raw).strip()
        try:
            date_str = datetime.strptime(clean_date, "%B %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            # Ambiguous date (e.g. "May 2026" or "2026") - skip
            skipped_dates += 1
            continue

        genre_list = _parse_genres(genre)

        results.append({
            "artist":       artist,
            "album":        album,
            "release_date": date_str,
            "genre":        ", ".join(genre_list),
            "genre_list":   genre_list,
            "type":         rel_type,
            "added_on":     added_on,
            "url":          album_url or artist_url,
            "source":       "MetalArchives",
        })

    if skipped_dates:
        print(f"\n[MetalArchives] Skipped {skipped_dates} rows with incomplete dates.")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_metal_archives_releases(mode: str = "pipeline") -> tuple[list[dict], bool]:
    """
    Paginate through the Metal Archives upcoming-releases endpoint.

    Returns (releases, complete). If a page fails after retries, the pages
    fetched so far are still returned with complete=False.
    """
    from_date, to_date = _build_date_range(mode)
    print(f"[MetalArchives] Mode: {mode} | Date range: {from_date} -> {to_date}")

    session = requests.Session()
    all_releases: list[dict] = []
    offset = 0
    echo   = 1
    complete = True

    for _ in range(MAX_PAGES):
        print(f"[MetalArchives] Fetching offset={offset} ...", end="\r")
        try:
            data = _fetch_page(session, from_date, to_date, offset, echo)
        except FetchError as exc:
            print(f"\n[MetalArchives] Request failed at offset={offset}: {exc}")
            complete = False
            break

        rows = data["aaData"]
        # In DataTables, iTotalDisplayRecords is the count after the date filter.
        try:
            total = int(data.get("iTotalDisplayRecords") or data.get("iTotalRecords") or 0)
        except (TypeError, ValueError):
            total = 0
        all_releases.extend(_parse_rows(rows))

        offset += PAGE_SIZE
        echo   += 1

        if not rows or (total and offset >= total):
            break

        # Be polite - Metal Archives rate-limits aggressively
        time.sleep(PAGE_DELAY)
    else:
        print(f"\n[MetalArchives] Stopped after {MAX_PAGES} pages (safety limit).")

    print(f"\n[MetalArchives] Fetched {len(all_releases)} releases"
          f"{'' if complete else ' (INCOMPLETE)'}.")
    return all_releases, complete


def merge_into_output(new_releases: list[dict]) -> None:
    """
    Load the existing metal_releases.json (if any), merge in the new
    Metal Archives entries (deduplicate on artist + normalised album),
    and save.

    Dedup strategy:
      - The dedup *lookup* key strips trailing parenthetical suffixes so
        that HeavyMusicHQ's "The Violence Of Time" matches Metal Archives'
        "THE VIOLENCE OF TIME (Heresies Against the Flow of Time)".
      - When a match is found, the Metal Archives entry wins: it carries
        the canonical title, genre, type, URL, and source.
    """
    # A missing file is fine; a corrupt one is NOT - treating it as empty
    # would silently wipe every other source's entries (and CI commits it).
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except FileNotFoundError:
        existing = []
    except json.JSONDecodeError as exc:
        raise SystemExit(f"[MetalArchives] {OUTPUT_FILE} is not valid JSON ({exc}); "
                         "refusing to overwrite it.")
    if not isinstance(existing, list):
        raise SystemExit(f"[MetalArchives] {OUTPUT_FILE} is not a JSON array; refusing to overwrite it.")

    albums_map: dict[tuple, dict] = {}
    unkeyed: list = []   # entries we can't key are kept verbatim, never dropped
    for item in existing:
        if not isinstance(item, dict) or not item.get("artist") or not item.get("album"):
            unkeyed.append(item)
            continue
        key = (str(item["artist"]).strip().lower(), _normalize_album_key(str(item["album"])))
        albums_map[key] = item

    added = 0
    for rel in new_releases:
        key = (rel["artist"].strip().lower(), _normalize_album_key(rel["album"]))
        if key not in albums_map:
            added += 1
        # Metal Archives wins: replace the entry entirely with the MA version.
        albums_map[key] = rel

    final = sorted(
        albums_map.values(),
        key=lambda x: str(x.get("release_date") or ""),
        reverse=True,
    ) + unkeyed

    if existing and len(final) < len(existing) * MIN_KEEP_RATIO:
        raise SystemExit(f"[MetalArchives] Merge would shrink {OUTPUT_FILE} from "
                         f"{len(existing)} to {len(final)} entries; refusing to write.")

    atomic_write_json(OUTPUT_FILE, final, indent=4)

    print(f"[MetalArchives] Merged {added} new entries -> {OUTPUT_FILE} "
          f"({len(final)} total).")


if __name__ == "__main__":
    _safe_console()

    parser = argparse.ArgumentParser(description="Metal Archives release scraper")
    parser.add_argument(
        "--mode",
        choices=["pipeline", "backfill"],
        default="pipeline",
        help="pipeline: -7d→+60d (CI default).  backfill: -14d→+60d (local seeding).",
    )
    args = parser.parse_args()

    releases, complete = fetch_metal_archives_releases(mode=args.mode)
    if releases:
        merge_into_output(releases)
    else:
        print("[MetalArchives] No releases fetched - nothing to merge.")

    if not complete:
        # Partial results (if any) were merged, but signal failure to CI / npm.
        sys.exit(1)
