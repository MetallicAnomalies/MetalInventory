import json
import feedparser
import cloudscraper
import time
from bs4 import BeautifulSoup
from datetime import datetime

def fetch_metal_reviews():
    # 1. Setup Scraper
    scraper = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

    # 2. List of Top Metal Review Feeds
    # (BangerTV removed, Metal Storm added)
    feeds = [
        {"name": "Angry Metal Guy", "url": "https://angrymetalguy.com/feed/"},
        {"name": "No Clean Singing", "url": "https://www.nocleansinging.com/category/reviews/feed/"},
        {"name": "Metal Injection", "url": "https://metalinjection.net/category/reviews/feed"},
        {"name": "MetalSucks", "url": "https://www.metalsucks.net/category/reviews/feed/"},
        {"name": "Invisible Oranges", "url": "https://www.invisibleoranges.com/category/reviews/feed/"},
        {"name": "Decibel Magazine", "url": "https://www.decibelmagazine.com/category/track-review/feed/"},
        {"name": "Metal Storm", "url": "http://www.metalstorm.net/rss/reviews.xml"}
    ]

    all_reviews = []
    seen_links = set()

    print(f"Fetching reviews from {len(feeds)} sources...\n")

    for source in feeds:
        print(f"--> Fetching: {source['name']}...")
        
        try:
            response = scraper.get(source['url'], timeout=15)
            
            if response.status_code == 200:
                feed = feedparser.parse(response.text)
                print(f"    Found {len(feed.entries)} entries.")

                for entry in feed.entries:
                    if entry.link in seen_links:
                        continue
                        
                    # --- DATE PARSING ---
                    try:
                        if hasattr(entry, 'published_parsed') and entry.published_parsed:
                            dt = datetime.fromtimestamp(time.mktime(entry.published_parsed))
                            date_str = dt.strftime('%Y-%m-%d')
                        else:
                            date_str = datetime.now().strftime('%Y-%m-%d')
                    except:
                        date_str = datetime.now().strftime('%Y-%m-%d')

                    # --- IMAGE EXTRACTION (The New Part) ---
                    image_url = ""
                    
                    # Strategy A: Check Media RSS tags (Standard for generic feeds)
                    if 'media_content' in entry and len(entry.media_content) > 0:
                        image_url = entry.media_content[0]['url']
                    elif 'media_thumbnail' in entry and len(entry.media_thumbnail) > 0:
                        image_url = entry.media_thumbnail[0]['url']
                    
                    # Strategy B: Check Enclosures (Standard for podcasts/news)
                    if not image_url and 'links' in entry:
                        for link in entry.links:
                            if 'image' in link.get('type', ''):
                                image_url = link.get('href', '')
                                break

                    # Strategy C: Scrape the HTML description (Common in blogs like AngryMetalGuy)
                    # Some feeds put the image inside <content:encoded> or <description>
                    if not image_url:
                        # Get the HTML content (prefer full content, fall back to summary)
                        content_html = ""
                        if 'content' in entry:
                            content_html = entry.content[0].value
                        else:
                            content_html = entry.get('summary', '') or entry.get('description', '')

                        if content_html:
                            try:
                                soup = BeautifulSoup(content_html, 'html.parser')
                                img_tag = soup.find('img')
                                if img_tag and img_tag.get('src'):
                                    image_url = img_tag['src']
                            except:
                                pass

                    # --- CLEAN SUMMARY ---
                    raw_summary = entry.get('summary', '') or entry.get('description', '')
                    # Remove HTML tags for a clean text summary
                    clean_summary = BeautifulSoup(raw_summary, "html.parser").get_text(" ", strip=True)

                    all_reviews.append({
                        "title": entry.title,
                        "link": entry.link,
                        "date": date_str,
                        "source": source['name'],
                        "author": entry.get('author', 'Unknown'),
                        "image_url": image_url,  # New Field
                        "summary": clean_summary[:300] + "..." if len(clean_summary) > 300 else clean_summary
                    })
                    seen_links.add(entry.link)
            else:
                print(f"    [Error] Status {response.status_code}")

        except Exception as e:
            print(f"    [Exception] {e}")
            continue
        
        time.sleep(1)

    # Sort & Save
    all_reviews.sort(key=lambda x: x['date'], reverse=True)
    
    with open('metal_reviews.json', 'w', encoding='utf-8') as f:
        json.dump(all_reviews, f, indent=4, ensure_ascii=False)

    print(f"\nSUCCESS: Saved {len(all_reviews)} reviews.")

if __name__ == "__main__":
    fetch_metal_reviews()