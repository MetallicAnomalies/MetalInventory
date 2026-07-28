import json
import time
import re
import cloudscraper
import requests

from datetime import datetime, timedelta

# --- CONFIGURATION ---
# MusicBrainz tag strings — used verbatim in Lucene tag: queries
MB_TAGS = [
    "black metal",
    "blackened death metal",
    "death metal",
    "melodic death metal",
    "progressive death metal",
    "doom metal",
    "gothic metal",
    "groove metal",
    "metalcore",
    "progressive metal",
    "power metal",
    "thrash metal",
    "symphonic metal",
    "heavy metal",
    "neoclassical metal",
    "industrial metal",
    "djent",
    # MA-rejected / borderline genres
    "nu metal",
    "mathcore",
    "math metal",
    "post-metal",
    "sludge metal",
    "stoner metal",
    "blackgaze",
    "ambient black metal",
]

def scrape_releases():
    # 1. Setup Scraper (cloudscraper used for browser-like SSL ciphers)
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    
    # 2. Setup Date Range
    today = datetime.now()
    current_year = today.year
    start_limit = today - timedelta(days=60)
    end_limit = today + timedelta(days=7)
    
    # MusicBrainz uses strict ISO dates for search queries
    mb_start = start_limit.strftime('%Y-%m-%d')
    mb_end = end_limit.strftime('%Y-%m-%d')
    
    print(f"SYSTEM DATE: {today.date()}")
    print(f"SEARCH RANGE: {start_limit.date()} to {end_limit.date()}\n")

    # Shared Dictionary for Deduplication: Key = (Title, Artist)
    albums_map = {}



   # =========================================================================
    # PART 2: MUSICBRAINZ (MB) SCRAPING (FIXED)
    # =========================================================================
    print("\n=== STARTING MUSICBRAINZ SCRAPE ===")
    
    # MusicBrainz is strict. We use a retry adapter and the scraper instance.
    mb_url = "https://musicbrainz.org/ws/2/release"
    
    # We update the headers on the scraper specifically for MB requests
    # Use a real-looking email or keep it formatted correctly
    mb_headers = {
        'User-Agent': 'scrobex/1.0 ( action@github.com )',
        'Accept': 'application/json'
    }

    for mb_tag in MB_TAGS:
        print(f">>> Querying MB Tag: '{mb_tag}'")
        
        query = (
            f'tag:"{mb_tag}" AND status:official AND (primarytype:Album OR primarytype:EP) '
            f'AND date:[{mb_start} TO {mb_end}]'
        )
        
        params = {
            'query': query,
            'fmt': 'json',
            'limit': 50
        }
        time.sleep(5)
        # RETRY LOGIC: Try 3 times before giving up on a genre
        for attempt in range(3):
            try:
                # CHANGED: Use 'scraper' instead of 'requests'
                # This uses browser-like SSL ciphers to prevent ConnectionReset
                resp = scraper.get(mb_url, headers=mb_headers, params=params, timeout=10)
                
                if resp.status_code == 200:
                    data = resp.json()
                    releases = data.get('releases', [])
                    print(f"   Found {len(releases)} results...")

                    for rel in releases:
                        title = rel.get('title', '').strip()
                        artist_credit = rel.get('artist-credit', [])
                        artist = artist_credit[0]['name'].strip() if artist_credit else "Unknown"
                        date_str = rel.get('date', '')

                        if len(date_str) == 10:
                            try:
                                rel_date = datetime.strptime(date_str, '%Y-%m-%d')
                            except ValueError: continue
                        else:
                            continue

                        if start_limit <= rel_date <= end_limit:
                            unique_key = (title.lower(), artist.lower())
                            clean_genre_title = mb_tag.title()

                            if unique_key in albums_map:
                                if clean_genre_title not in albums_map[unique_key]['genre_list']:
                                    albums_map[unique_key]['genre_list'].append(clean_genre_title)
                                    albums_map[unique_key]['genre'] = ", ".join(albums_map[unique_key]['genre_list'])
                            else:
                                mb_link = f"https://musicbrainz.org/release/{rel['id']}"
                                albums_map[unique_key] = {
                                    "artist": artist,
                                    "album": title,
                                    "release_date": rel_date.strftime('%Y-%m-%d'),
                                    "genre": clean_genre_title,
                                    "genre_list": [clean_genre_title],
                                    "url": mb_link,
                                    "source": "MusicBrainz"
                                }
                    # Break the retry loop if successful
                    break 
                
                elif resp.status_code == 503:
                    # 503 means "Slow down", so we wait longer and retry
                    print("   [503] Rate limit hit. Waiting 5s...")
                    time.sleep(5)
                    continue
                else:
                    print(f"   [Error] MB Status {resp.status_code}")
                    break

            except Exception as e:
                print(f"   [Attempt {attempt+1}] Connection error: {e}")
                time.sleep(2) # Wait a bit before retry

        # RATE LIMITING: Valid requests still need a pause
        time.sleep(1.5)
    # =========================================================================
    # SAVE OUTPUT
    # =========================================================================
    try:
        with open('metal_releases.json', 'r', encoding='utf-8') as f:
            existing_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        existing_data = []

    existing_map = {}
    for item in existing_data:
        artist_norm = item.get('artist', '').lower().strip()
        album_norm = item.get('album', '').lower().strip()
        if artist_norm and album_norm:
            existing_map[(artist_norm, album_norm)] = item

    for item in albums_map.values():
        item_copy = item.copy()
        if 'genre_list' in item_copy:
            del item_copy['genre_list']
        
        artist_norm = item_copy.get('artist', '').lower().strip()
        album_norm = item_copy.get('album', '').lower().strip()
        
        # Overwrite or add
        existing_map[(artist_norm, album_norm)] = item_copy

    final_output = list(existing_map.values())
    final_output.sort(key=lambda x: x.get('release_date', '0000-00-00'), reverse=True)

    with open('metal_releases2.json', 'w', encoding='utf-8') as f:
        json.dump(final_output, f, indent=4, ensure_ascii=False)
    
    print(f"\nSUCCESS: Saved {len(final_output)} unique albums.")

if __name__ == "__main__":
    scrape_releases()