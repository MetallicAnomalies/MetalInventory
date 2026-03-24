import requests
from bs4 import BeautifulSoup
import json
from datetime import date
import time
import re

BASE_URL = "https://metalstorm.net/events/new_releases.php"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

def fetch_metalstorm_releases(session: requests.Session, page: int = 1) -> list[dict]:
    """Fetch releases from a specific page."""
    params = {"page": page}
    response = session.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
    response.raise_for_status()
    
    soup = BeautifulSoup(response.text, "html.parser")
    
    # Find the main releases table
    releases_table = soup.find("div", class_="listContainer")
    if not releases_table:
        return []
    
    releases = []
    
    # Each release is in a div with class "releaseItem"
    for item in releases_table.find_all("div", class_="releaseItem"):
        try:
            # Album cover
            cover_img = item.find("img", class_="releaseCover")
            cover_url = cover_img["src"] if cover_img else ""
            
            # Album info container
            info_div = item.find("div", class_="releaseInfo")
            if not info_div:
                continue
                
            # Album title and link
            album_link = info_div.find("a", class_="bold")
            album_name = album_link.get_text(strip=True) if album_link else "Unknown"
            album_url = "https://metalstorm.net" + album_link["href"] if album_link and album_link.get("href") else ""
            
            # Band name
            band_link = info_div.find("a", href=re.compile(r"/bands/"))
            band_name = band_link.get_text(strip=True) if band_link else "Unknown"
            band_url = "https://metalstorm.net" + band_link["href"] if band_link else ""
            
            # Release date
            date_span = info_div.find("span", class_="date")
            release_date = date_span.get_text(strip=True) if date_span else "Unknown"
            
            # Genre (usually after band name)
            genre_elem = item.find("div", string=re.compile(r"(Black|Death|Doom|Thrash|Power|Prog)", re.I))
            genre = genre_elem.get_text().strip() if genre_elem else "Unknown"
            
            releases.append({
                "band_name": band_name,
                "band_url": band_url,
                "album_name": album_name,
                "album_url": album_url,
                "cover_url": cover_url,
                "release_date": release_date,
                "genre": genre,
                "source": "metalstorm",
            })
            
        except Exception as e:
            print(f"Error parsing release item: {e}")
            continue
    
    return releases

def fetch_all_metalstorm_releases(max_pages: int = 5) -> list[dict]:
    """Fetch releases from multiple pages."""
    all_releases = []
    session = requests.Session()
    
    for page in range(1, max_pages + 1):
        print(f"Fetching MetalStorm page {page}...")
        page_releases = fetch_metalstorm_releases(session, page)
        all_releases.extend(page_releases)
        
        if len(page_releases) < 20:  # Less results = probably last page
            break
            
        time.sleep(1)  # Be respectful
    
    return all_releases

def save_releases(filename: str, releases: list[dict]):
    """Save releases to JSON file."""
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(releases, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(releases)} releases to {filename}")

if __name__ == "__main__":
    releases = fetch_all_metalstorm_releases(max_pages=3)
    
    print(f"\n🎵 Found {len(releases)} new releases on MetalStorm:")
    for r in releases[:5]:  # Show first 5
        print(f"  {r['release_date']} - {r['band_name']} - {r['album_name']}")
    
    save_releases("metalstorm_releases.json", releases)
