import json
import os
import sys

# Import normalize functions from enrich.py
from enrich import normalize, shard_key

def main():
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    releases_path = os.path.join(workspace, "metal_releases.json")
    shards_dir = os.path.join(workspace, "metadata", "shards")
    
    print(f"Loading releases from {releases_path}...")
    with open(releases_path, 'r', encoding='utf-8') as f:
        releases = json.load(f)
        
    # Group new releases by shard key
    shards_to_update = {}
    for release in releases:
        artist = release.get("artist")
        if not artist:
            continue
            
        norm_name = normalize(artist)
        s_key = shard_key(norm_name)
        
        if s_key not in shards_to_update:
            shards_to_update[s_key] = []
        shards_to_update[s_key].append(release)
        
    print(f"Found {len(releases)} releases, spanning {len(shards_to_update)} shards.")
    
    updated_shards = 0
    added_artists = 0
    added_albums = 0
    
    for s_key, rels in shards_to_update.items():
        shard_path = os.path.join(shards_dir, f"base-{s_key}.json")
        if os.path.exists(shard_path):
            with open(shard_path, 'r', encoding='utf-8') as f:
                shard_data = json.load(f)
        else:
            shard_data = []
            
        # Create map of existing artists for quick lookup
        artist_map = {entry["normalizedName"]: entry for entry in shard_data}
        
        shard_modified = False
        
        for release in rels:
            artist_name = release.get("artist")
            album_name = release.get("album")
            release_date = release.get("release_date")
            genre_str = release.get("genre", "")
            
            if not artist_name or not album_name:
                continue
                
            norm_name = normalize(artist_name)
            
            # Extract year from YYYY-MM-DD
            year = ""
            if release_date:
                year = release_date.split("-")[0]
                
            # Process genres
            genres = [g.strip() for g in genre_str.split(",") if g.strip()]
            
            # Find or create artist entry
            if norm_name not in artist_map:
                new_artist = {
                    "normalizedName": norm_name,
                    "displayName": artist_name,
                    "albums": [],
                    "tags": []
                }
                shard_data.append(new_artist)
                artist_map[norm_name] = new_artist
                added_artists += 1
                shard_modified = True
                
            artist_entry = artist_map[norm_name]
            
            # Check if album already exists (by normalized name to avoid duplicates)
            norm_new_album = normalize(album_name)
            album_exists = False
            for album in artist_entry.get("albums", []):
                if normalize(album.get("name", "")) == norm_new_album:
                    album_exists = True
                    break
                    
            if not album_exists:
                new_album = {
                    "name": album_name,
                    "year": year,
                    "genres": genres
                }
                artist_entry.setdefault("albums", []).append(new_album)
                
                # Extend artist tags
                for g in genres:
                    if g not in artist_entry.setdefault("tags", []):
                        artist_entry["tags"].append(g)
                        
                added_albums += 1
                shard_modified = True
                
        if shard_modified:
            # Sort artists back by normalizedName to keep it clean
            shard_data.sort(key=lambda x: x["normalizedName"])
            with open(shard_path, 'w', encoding='utf-8') as f:
                json.dump(shard_data, f, indent=2, ensure_ascii=False)
            updated_shards += 1

    print(f"Done. Updated {updated_shards} shards.")
    print(f"Added {added_artists} new artists and {added_albums} new albums.")

if __name__ == "__main__":
    main()
