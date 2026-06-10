import json
import os
from datetime import datetime, timedelta

def cleanup_old_releases(filename="metal_releases.json", days_old=30):
    if not os.path.exists(filename):
        print(f"File {filename} not found.")
        return

    with open(filename, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            print(f"Error reading JSON from {filename}.")
            return

    cutoff_date = datetime.now() - timedelta(days=days_old)
    
    cleaned_data = []
    removed_count = 0

    for item in data:
        release_date_str = item.get("release_date", "")
        # If release_date is missing or invalid, we keep it to be safe, 
        # or we could attempt to parse it. 
        if release_date_str:
            try:
                release_date = datetime.strptime(release_date_str, "%Y-%m-%d")
                if release_date >= cutoff_date:
                    cleaned_data.append(item)
                else:
                    removed_count += 1
            except ValueError:
                # Keep items with unparseable dates
                cleaned_data.append(item)
        else:
            cleaned_data.append(item)

    if removed_count > 0:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(cleaned_data, f, indent=4, ensure_ascii=False)
        print(f"Cleaned up {removed_count} releases older than {days_old} days. Kept {len(cleaned_data)} releases.")
    else:
        print(f"No releases older than {days_old} days found. Kept {len(cleaned_data)} releases.")

if __name__ == "__main__":
    cleanup_old_releases("metal_releases.json", 30)
