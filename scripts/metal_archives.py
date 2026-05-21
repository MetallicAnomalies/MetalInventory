"""
Metal Archives Upcoming Releases Scraper
========================================
Fetches upcoming metal releases from the Metal Archives AJAX endpoint
and merges them into the existing metal_releases.json file.

Date range: last 2 weeks → next 2 months (relative to today at runtime).
"""

import json
import time
import re
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MA_AJAX_URL = (
    "https://www.metal-archives.com/release/ajax-upcoming/json/1"
)

MA_HEADERS = {
    # Metal Archives requires a browser-like User-Agent to avoid 403s
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.metal-archives.com/",
}

OUTPUT_FILE = "metal_releases.json"

# Pagination: MA returns at most 100 rows per request
PAGE_SIZE = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_html(raw: str) -> str:
    """Remove HTML tags from a Metal Archives cell value."""
    return BeautifulSoup(raw, "html.parser").get_text(strip=True)


def _extract_ma_link(raw: str) -> str:
    """Pull the href out of an anchor tag, or return empty string."""
    soup = BeautifulSoup(raw, "html.parser")
    tag = soup.find("a")
    return tag["href"] if tag and tag.get("href") else ""


def _build_date_range() -> tuple[str, str]:
    """
    Returns (from_date, to_date) as 'YYYY-MM-DD' strings.

    from_date : today  -  14 days  (last 2 weeks)
    to_date   : today  +  60 days  (next 2 months)
    """
    today = datetime.utcnow()
    from_date = (today - timedelta(weeks=2)).strftime("%Y-%m-%d")
    to_date   = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    return from_date, to_date


def _fetch_page(session: requests.Session, from_date: str, to_date: str,
                offset: int, echo: int) -> dict:
    """Fetch a single page from the Metal Archives AJAX endpoint."""
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
        "_":              int(datetime.utcnow().timestamp() * 1000),
    }

    resp = session.get(MA_AJAX_URL, headers=MA_HEADERS, params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _parse_rows(rows: list[list]) -> list[dict]:
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
    for row in rows:
        try:
            artist      = _strip_html(row[0])
            album       = _strip_html(row[1])
            rel_type    = _strip_html(row[2])
            genre       = _strip_html(row[3])
            date_raw    = _strip_html(row[4])
            added_on    = row[5].strip() if isinstance(row[5], str) else ""
            artist_url  = _extract_ma_link(row[0])
            album_url   = _extract_ma_link(row[1])

            # Parse date like "May 8th, 2026"
            clean_date = re.sub(r'(\d+)(st|nd|rd|th)', r'\1', date_raw).strip()
            try:
                rel_date = datetime.strptime(clean_date, "%B %d, %Y")
                date_str = rel_date.strftime("%Y-%m-%d")
            except ValueError:
                # If date is ambiguous (e.g. only year), skip
                continue

            results.append({
                "artist":       artist,
                "album":        album,
                "release_date": date_str,
                "genre":        genre,
                "type":         rel_type,
                "added_on":     added_on,
                "url":          album_url or artist_url,
                "source":       "MetalArchives",
            })
        except (IndexError, KeyError, TypeError):
            continue

    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_metal_archives_releases() -> list[dict]:
    """
    Paginate through the Metal Archives upcoming-releases endpoint and
    return a list of normalised release dicts.
    """
    from_date, to_date = _build_date_range()
    print(f"[MetalArchives] Date range: {from_date} → {to_date}")

    session = requests.Session()
    all_releases: list[dict] = []
    offset = 0
    echo   = 1

    while True:
        print(f"[MetalArchives] Fetching offset={offset} …", end="\r")
        try:
            data = _fetch_page(session, from_date, to_date, offset, echo)
        except requests.RequestException as exc:
            print(f"\n[MetalArchives] Request failed at offset={offset}: {exc}")
            break

        rows        = data.get("aaData", [])
        total       = data.get("iTotalRecords", 0)
        page_releases = _parse_rows(rows)
        all_releases.extend(page_releases)

        offset += PAGE_SIZE
        echo   += 1

        if offset >= total or not rows:
            break

        # Be polite – Metal Archives rate-limits aggressively
        time.sleep(1.5)

    print(f"\n[MetalArchives] Fetched {len(all_releases)} releases.")
    return all_releases


def merge_into_output(new_releases: list[dict]) -> None:
    """
    Load the existing metal_releases.json (if any), merge in the new
    Metal Archives entries (deduplicate on artist+album), and save.
    """
    # Load existing data
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []

    # Build dedup map keyed on (lower artist, lower album)
    albums_map: dict[tuple, dict] = {
        (item["artist"].lower(), item["album"].lower()): item
        for item in existing
    }

    added = 0
    for rel in new_releases:
        key = (rel["artist"].lower(), rel["album"].lower())
        if key not in albums_map:
            albums_map[key] = rel
            added += 1
        else:
            # Optionally enrich existing entry with MA source info
            entry = albums_map[key]
            if "MetalArchives" not in entry.get("source", ""):
                entry["source"] = entry.get("source", "") + ", MetalArchives"

    final = sorted(
        albums_map.values(),
        key=lambda x: x.get("release_date", ""),
        reverse=True,
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=4, ensure_ascii=False)

    print(f"[MetalArchives] Merged {added} new entries → {OUTPUT_FILE} "
          f"({len(final)} total).")


if __name__ == "__main__":
    releases = fetch_metal_archives_releases()
    if releases:
        merge_into_output(releases)
    else:
        print("[MetalArchives] No releases fetched – nothing to merge.")
