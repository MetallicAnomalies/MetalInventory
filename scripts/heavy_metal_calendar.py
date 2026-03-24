import requests
from bs4 import BeautifulSoup
import json
import re
import os
from datetime import datetime

# ==============================================================================
# CONFIGURATION
# ==============================================================================
JSON_FILE = 'metal_releases.json'
URL = "https://heavymusichq.com/heavy-metal-album-release-calendar/"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8'
}

# ==============================================================================
# FUNCTIONS
# ==============================================================================

def load_existing_data(filename):
    """
    Loads existing JSON. Handles both 'artist' and 'band' keys to ensure
    we don't create duplicates if the schema changed.
    """
    if not os.path.exists(filename):
        print(f"File {filename} not found. Starting fresh.")
        return {}, []

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            data_list = json.load(f)
            
        albums_map = {}
        for entry in data_list:
            # SAFETY: Check for 'artist' OR 'band' key
            artist_val = entry.get('artist') or entry.get('band') or ''
            album_val = entry.get('album', '')
            
            # Normalize for comparison (lowercase, stripped)
            artist_norm = artist_val.strip().lower()
            album_norm = album_val.strip().lower()
            
            if artist_norm and album_norm:
                albums_map[(artist_norm, album_norm)] = entry
        
        print(f"Loaded {len(data_list)} existing entries from {filename}.")
        return albums_map, data_list
    except Exception as e:
        print(f"Error loading {filename}: {e}")
        return {}, []

def save_data(filename, data_list):
    """
    Saves the list back to JSON.
    """
    # Sort by date (Newest first), handling potential missing dates
    try:
        data_list.sort(key=lambda x: x.get('release_date', '0000-00-00'), reverse=True)
    except:
        pass

    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data_list, f, indent=4, ensure_ascii=False)
        print(f"Successfully saved {len(data_list)} total releases to {filename}.")
    except Exception as e:
        print(f"Error saving file: {e}")

def scrape_calendar():
    print(f"Fetching {URL}...")
    try:
        response = requests.get(URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
    except Exception as e:
        print(f"Failed to fetch page: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    content_div = soup.find('div', class_='entry-content')

    if not content_div:
        print("Error: Could not find main content div.")
        return []

    new_entries = []
    current_date_str = None

    # Iterate through content to find Headers (Dates) and Paragraphs (Releases)
    for element in content_div.find_all(['p', 'h3', 'div']):
        text = element.get_text().strip()
        
        # 1. Detect Date Header (e.g., "February 6, 2026")
        date_match = re.search(r'([A-Z][a-z]+ \d{1,2}, \d{4})', text)
        if date_match and len(text) < 40:
            try:
                raw_date = date_match.group(1)
                # Parse "February 6, 2026" -> "2026-02-06"
                dt_obj = datetime.strptime(raw_date, "%B %d, %Y")
                current_date_str = dt_obj.strftime("%Y-%m-%d")
                continue
            except ValueError:
                pass

        # 2. Parse Releases (if we have a valid date)
        if current_date_str and ("–" in text or "-" in text):
            # Split lines by <br> tag (HMHQ often groups multiple bands per date block)
            lines = element.decode_contents().split('<br/>')
            
            for line in lines:
                # Clean up HTML tags
                line_soup = BeautifulSoup(line, 'html.parser')
                clean_line = line_soup.get_text().strip()
                
                if not clean_line:
                    continue

                # Normalize separator (En-dash vs Hyphen)
                delimiter = "–" if "–" in clean_line else "-"
                if delimiter not in clean_line:
                    continue

                # Split Artist - Album
                parts = clean_line.split(delimiter, 1)
                if len(parts) < 2:
                    continue

                artist_name = parts[0].strip()
                album_raw = parts[1].strip()

                # Remove Label info: "Album Name (Label)" -> "Album Name"
                if "(" in album_raw and album_raw.endswith(")"):
                    album_name = album_raw.rsplit("(", 1)[0].strip()
                else:
                    album_name = album_raw

                # Create Entry Object matching your requested structure
                entry = {
                    "artist": artist_name,       # Changed from 'band' to 'artist'
                    "album": album_name,
                    "release_date": current_date_str,
                    "genre": "",                 # Empty
                    "url": "",                   # Empty
                    "source": "Heavy Music HQ"   # Added Source
                }
                new_entries.append(entry)

    return new_entries

# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
def main():
    # 1. Load Existing Data
    existing_map, full_list = load_existing_data(JSON_FILE)
    
    # 2. Scrape New Data
    candidates = scrape_calendar()
    print(f"Scraper found {len(candidates)} candidates on the website.")

    # 3. Merge (Add only if not in existing_map)
    added_count = 0
    for entry in candidates:
        key = (entry['artist'].lower().strip(), entry['album'].lower().strip())
        
        if key not in existing_map:
            full_list.append(entry)
            existing_map[key] = entry # Update map to prevent duplicates within the new batch
            added_count += 1
            print(f"   [NEW] {entry['artist']} - {entry['album']}")

    if added_count == 0:
        print("No new releases found. File is up to date.")
    else:
        print(f"\nAdding {added_count} new releases to database...")
        save_data(JSON_FILE, full_list)

if __name__ == "__main__":
    main()