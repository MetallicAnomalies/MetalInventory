import json
import requests
import os
import sys
import shutil
import time
from PIL import Image, ImageDraw, ImageFont

# --- CONFIGURATION ---
INPUT_FILE = "collage_metadata.json"
OUTPUT_FILE = "scrobex_collage.jpg"
CACHE_DIR = "downloaded_images"  # Folder to store downloaded images

# Grid Settings
GRID_SIZE = 10     # 10x10 grid
CELL_SIZE = 300    # 300px per image
GRID_PIXELS = GRID_SIZE * CELL_SIZE # 3000px

# Legend Settings
LEGEND_WIDTH = 900
LEGEND_BG_COLOR = (20, 20, 20)
TEXT_COLOR = (230, 230, 230)

def ensure_dir(directory):
    if not os.path.exists(directory):
        os.makedirs(directory)

def get_local_filename(rank, artist):
    """
    Creates a safe filename like '001_artist_name.jpg'
    """
    safe_artist = "".join([c for c in artist if c.isalnum() or c in (' ', '-', '_')]).strip()
    return os.path.join(CACHE_DIR, f"{rank:03d}_{safe_artist}.jpg")

def main():
    print(f"Reading {INPUT_FILE}...")
    try:
        with open(INPUT_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        sys.exit(f"CRITICAL ERROR: {INPUT_FILE} not found.")

    target_count = GRID_SIZE * GRID_SIZE
    successful_items = []
    failed_items = []

    print(f"--- CHECKING / DOWNLOADING IMAGES (Target: {target_count} items) ---")
    ensure_dir(CACHE_DIR)

    for index, item in enumerate(data):
        if len(successful_items) >= target_count:
            break

        rank = item.get('rank', index + 1)
        artist = item.get('artist', 'Unknown')
        url = item.get('imageUrl', '')

        local_path = get_local_filename(rank, artist)

        # Check if local image already exists and is valid
        exists = False
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            exists = True

        if exists:
            print(f"{local_path}")
            print(f"[{rank}] Exists: {artist}")
            successful_items.append(item)
            continue

        if not url:
            print(f"{local_path}")
            print(f"[{rank}] SKIPPING: No URL provided for {artist}")
            failed_items.append((rank, artist, "No URL"))
            continue

        print(f"{local_path}")
        print(f"[{rank}] Downloading: {artist}...")
        try:
            response = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            response.raise_for_status()
            with open(local_path, 'wb') as f:
                f.write(response.content)
            successful_items.append(item)
            time.sleep(1)
        except Exception as e:
            print(f"    !!! FAILED: {e}")
            failed_items.append((rank, artist, str(e)))

    # Print failed log if any
    if failed_items:
        print("\n" + "="*40)
        print(f"NOTE: {len(failed_items)} images failed to download.")
        print("="*40)
        for rank, artist, reason in failed_items:
            print(f"Rank {rank} ({artist}): {reason}")

    # If we don't have enough items
    if len(successful_items) < target_count:
        print("\n" + "="*40)
        print(f"WARNING: Only {len(successful_items)} / {target_count} images could be successfully downloaded.")
        print("="*40)
        user_input = input("\nDo you want to continue generating the collage anyway? (Remaining slots will be black placeholders) [y/N]: ")
        if user_input.lower() != 'y':
            sys.exit("Script stopped. Check URLs or connection and run again.")

    # Use the successful ones
    items = successful_items[:target_count]

    # --- PHASE 2: GENERATE COLLAGE ---
    print(f"\nInitializing {GRID_PIXELS}x{GRID_PIXELS}px grid canvas...")
    collage_img = Image.new('RGB', (GRID_PIXELS, GRID_PIXELS), color=(0, 0, 0))

    print("Processing images from cache...")
    for index, item in enumerate(items):
        row = index // GRID_SIZE
        col = index % GRID_SIZE
        x_pos = col * CELL_SIZE
        y_pos = row * CELL_SIZE
        
        rank = item.get('rank', index + 1)
        artist = item.get('artist', 'Unknown')
        local_path = get_local_filename(rank, artist)

        try:
            # Try to open the local file
            if os.path.exists(local_path):
                img = Image.open(local_path).convert('RGB')
                img = img.resize((CELL_SIZE, CELL_SIZE), Image.Resampling.LANCZOS)
                collage_img.paste(img, (x_pos, y_pos))
            else:
                # If valid, but file missing (user opted to skip download errors)
                # Draw a placeholder box
                draw = ImageDraw.Draw(collage_img)
                draw.rectangle([x_pos, y_pos, x_pos+CELL_SIZE, y_pos+CELL_SIZE], fill=(50,0,0), outline=(255,0,0))
                draw.text((x_pos+10, y_pos+10), "MISSING", fill=(255,255,255))
                
        except Exception as e:
            print(f"Error processing image {rank} ({artist}): {e}")

    # --- PHASE 3: SIDE LEGEND ---
    print("Generating Sidebar Legend...")
    
    line_height = GRID_PIXELS // len(items)
    font_size = int(line_height * 0.7)
    
    legend_img = Image.new('RGB', (LEGEND_WIDTH, GRID_PIXELS), color=LEGEND_BG_COLOR)
    draw = ImageDraw.Draw(legend_img)
    
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except IOError:
        font = ImageFont.load_default()

    for index, item in enumerate(items):
        rank = item.get('rank', index + 1)
        artist = item.get('artist', 'Unknown')
        album = item.get('album', 'Unknown')
        
        text = f"{rank}. {artist} - {album}"
        y = index * line_height
        y_padding = (line_height - font_size) // 2
        
        max_chars = 60
        if len(text) > max_chars:
            text = text[:max_chars] + "..."
            
        draw.text((30, y + y_padding), text, fill=TEXT_COLOR, font=font)

    # --- PHASE 4: SAVE ---
    print("Combining Grid and Legend...")
    final_width = GRID_PIXELS + LEGEND_WIDTH
    final_image = Image.new('RGB', (final_width, GRID_PIXELS))
    
    final_image.paste(collage_img, (0, 0))
    final_image.paste(legend_img, (GRID_PIXELS, 0))

    print(f"Saving to {OUTPUT_FILE}...")
    final_image.save(OUTPUT_FILE, quality=90)
    print("Success! Open the file to view results.")

if __name__ == "__main__":
    main()