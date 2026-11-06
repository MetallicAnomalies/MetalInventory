"""
Rainbow collage generator: downloads album art, sorts it by colour into a
gradient, and renders a GRID_SIZE x GRID_SIZE collage with a numbered legend.

Defaults live in the CONFIGURATION block; every one can be overridden on the
command line (run with --help). Relative paths resolve against this script's
folder, so it works from any working directory.

Robustness notes:
  - The image cache is keyed by a hash of the image URL, so two inputs (or two
    albums by the same artist) can never reuse each other's cover.
  - Cached and freshly downloaded files are verified with Pillow; corrupt or
    non-image downloads (e.g. an HTML error page) are discarded and retried.
  - Downloads retry with backoff and are written atomically.
  - Colour sorting uses a saturation-weighted hue, and near-greyscale covers
    (common in metal) are grouped at the end, light to dark, instead of being
    scattered through the rainbow by a meaningless hue.
"""

import argparse
import colorsys
import hashlib
import json
import math
import os
import sys
import tempfile
import time

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

# --- CONFIGURATION ---
INPUT_FILE = "collage_metadata recent albums.json"
OUTPUT_FILE = "scrobex_collage_2026_10.albums.jpg"
CACHE_DIR = "downloaded_images_albums"  # Folder to store downloaded images

# Grid Settings
GRID_SIZE = 20     # 20x20 grid
CELL_SIZE = 300    # 300px per image

# Rainbow Settings
RAINBOW_SORT = True        # False = keep original rank order
SORT_BY = "hue"            # "hue" (colour rainbow) or "brightness" (light -> dark)
FILL_ORDER = "diagonal"    # "horizontal" (row by row) or "diagonal"
GREY_THRESHOLD = 0.10      # colourfulness (0-1) below which a cover counts as greyscale

# Legend Settings
LEGEND_WIDTH = 900
LEGEND_BG_COLOR = (20, 20, 20)
TEXT_COLOR = (230, 230, 230)
MAX_FONT_SIZE = 40

# Download Settings
DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_TIMEOUT = (10, 30)
DOWNLOAD_DELAY = 1.0
USER_AGENT = "Mozilla/5.0"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def resolve(path):
    return path if os.path.isabs(path) else os.path.join(SCRIPT_DIR, path)


def safe_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


# ---------------------------------------------------------------------------
# Cache / download
# ---------------------------------------------------------------------------

def cache_path(cache_dir, artist, url):
    """'<artist>_<urlhash>.jpg' - unique per image URL, readable for humans.
    (The bytes may be PNG/WebP; Pillow detects the format from content.)"""
    safe_artist = "".join(c for c in artist if c.isalnum() or c in (" ", "-", "_")).strip()[:40]
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return os.path.join(cache_dir, f"{safe_artist or 'unknown'}_{digest}.jpg")


def is_valid_image(path):
    try:
        with Image.open(path) as img:
            img.verify()
        return True
    except Exception:
        return False


def download_image(session, url, dest):
    """Download url to dest atomically. Returns None on success, else an error string."""
    last_error = "unknown error"
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            resp = session.get(url, timeout=DOWNLOAD_TIMEOUT)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise requests.HTTPError(f"HTTP {resp.status_code}")
            if resp.status_code >= 400:
                return f"HTTP {resp.status_code}"  # 404 etc.: retrying won't help
            if not resp.content:
                raise ValueError("empty response")

            fd, tmp = tempfile.mkstemp(prefix=".dl-", dir=os.path.dirname(dest))
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(resp.content)
                if not is_valid_image(tmp):
                    raise ValueError(f"not an image ({resp.headers.get('Content-Type', '?')})")
                os.replace(tmp, dest)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return None
        except (requests.RequestException, ValueError, OSError) as e:
            last_error = str(e)
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(2 ** attempt)
    return last_error


def fetch_all(data, cache_dir):
    """Ensure every item's image is cached. Returns (ok_entries, failures)."""
    os.makedirs(cache_dir, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    ok, failed = [], []
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            failed.append((index + 1, "?", "malformed entry"))
            continue
        rank = item.get("rank", index + 1)
        artist = str(item.get("artist") or "Unknown")
        url = str(item.get("imageUrl") or "").strip()

        if not url:
            print(f"[{rank}] SKIPPING: No URL provided for {artist}")
            failed.append((rank, artist, "No URL"))
            continue

        path = cache_path(cache_dir, artist, url)
        if os.path.exists(path):
            if is_valid_image(path):
                ok.append({"item": item, "rank": rank, "path": path})
                continue
            print(f"[{rank}] Cached file is corrupt, re-downloading: {artist}")
            os.remove(path)

        print(f"[{rank}] Downloading: {artist}...")
        error = download_image(session, url, path)
        if error:
            print(f"    !!! FAILED: {error}")
            failed.append((rank, artist, error))
        else:
            ok.append({"item": item, "rank": rank, "path": path})
            time.sleep(DOWNLOAD_DELAY)

    return ok, failed


# ---------------------------------------------------------------------------
# Colour analysis / ordering
# ---------------------------------------------------------------------------

def color_profile(path):
    """
    Returns (hue, colourfulness, brightness), each 0-1.

    Hue is the circular mean of pixel hues weighted by chroma (s*v), so a
    cover with a red logo on black reads as red rather than as the muddy
    average colour. Colourfulness is the mean chroma times how concentrated
    the hues are; low values mean greyscale or a hue soup.
    """
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB").resize((64, 64))
        pixels = list(img.convert("HSV").getdata())

    x = y = weight_sum = v_sum = 0.0
    for h, s, v in pixels:
        w = (s / 255) * (v / 255)
        angle = (h / 255) * 2 * math.pi
        x += w * math.cos(angle)
        y += w * math.sin(angle)
        weight_sum += w
        v_sum += v / 255

    n = len(pixels)
    brightness = v_sum / n
    if weight_sum == 0:
        return 0.0, 0.0, brightness
    hue = (math.atan2(y, x) / (2 * math.pi)) % 1.0
    concentration = math.hypot(x, y) / weight_sum
    colourfulness = (weight_sum / n) * concentration
    return hue, colourfulness, brightness


def sort_by_color(entries, sort_by, grey_threshold):
    """
    hue:        colourful covers by hue (red -> violet), then greyscale
                covers light -> dark. Unreadable images go last.
    brightness: everything light -> dark.
    """
    print(f"--- ANALYZING COLORS (sort by {sort_by}) ---")
    keyed = []
    for e in entries:
        try:
            hue, colourfulness, brightness = color_profile(e["path"])
        except Exception as ex:
            print(f"    Could not analyze {e['item'].get('artist', '?')}: {ex}")
            keyed.append(((2, 0.0), e))
            continue
        if sort_by == "brightness":
            key = (0, 1 - brightness)
        elif colourfulness >= grey_threshold:
            key = (0, hue)
        else:
            key = (1, 1 - brightness)
        keyed.append((key, e))

    greys = sum(1 for k, _ in keyed if k[0] == 1)
    if sort_by == "hue":
        print(f"    {len(keyed) - greys} colourful, {greys} greyscale covers")
    keyed.sort(key=lambda pair: pair[0])
    return [e for _, e in keyed]


def diagonal_positions(size):
    """(row, col) positions in top-left -> bottom-right diagonal order."""
    positions = []
    for diag in range(2 * size - 1):
        for row in range(size):
            col = diag - row
            if 0 <= col < size:
                positions.append((row, col))
    return positions


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def load_font(size):
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)   # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default()


def fit_text(draw, text, font, max_width):
    """Truncate text with an ellipsis so it fits max_width pixels."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=font) > max_width:
        text = text[:-1]
    return text.rstrip() + "..."


def render(entries, args):
    grid_pixels = args.grid_size * args.cell_size
    print(f"\nInitializing {grid_pixels}x{grid_pixels}px grid canvas...")
    collage = Image.new("RGB", (grid_pixels, grid_pixels), color=(0, 0, 0))

    if args.fill_order == "diagonal":
        positions = diagonal_positions(args.grid_size)
    else:
        positions = [(i // args.grid_size, i % args.grid_size)
                     for i in range(args.grid_size * args.grid_size)]

    print("Processing images from cache...")
    for index, e in enumerate(entries):
        row, col = positions[index]
        x, y = col * args.cell_size, row * args.cell_size
        try:
            with Image.open(e["path"]) as img:
                img = ImageOps.exif_transpose(img).convert("RGB")
                # Centre-crop non-square art instead of stretching it.
                img = ImageOps.fit(img, (args.cell_size, args.cell_size), Image.Resampling.LANCZOS)
                collage.paste(img, (x, y))
        except Exception as ex:
            print(f"Error processing image {e['rank']} ({e['item'].get('artist', '?')}): {ex}")
            draw = ImageDraw.Draw(collage)
            draw.rectangle([x, y, x + args.cell_size - 1, y + args.cell_size - 1],
                           fill=(50, 0, 0), outline=(255, 0, 0))
            draw.text((x + 10, y + 10), "MISSING", fill=(255, 255, 255))

    print("Generating Sidebar Legend...")
    legend = Image.new("RGB", (args.legend_width, grid_pixels), color=LEGEND_BG_COLOR)
    draw = ImageDraw.Draw(legend)
    line_height = grid_pixels // max(len(entries), 1)
    font_size = max(6, min(int(line_height * 0.7), MAX_FONT_SIZE))
    font = load_font(font_size)

    # Number each item by its grid cell (row-major, 1-based), list in that order.
    numbered = sorted(
        ((positions[i][0] * args.grid_size + positions[i][1] + 1, e) for i, e in enumerate(entries)),
        key=lambda pair: pair[0],
    )
    max_text_width = args.legend_width - 60
    for line, (grid_num, e) in enumerate(numbered):
        item = e["item"]
        text = f"{grid_num}. {item.get('artist', 'Unknown')} - {item.get('album', 'Unknown')}"
        text = fit_text(draw, text, font, max_text_width)
        y = line * line_height + (line_height - font_size) // 2
        draw.text((30, y), text, fill=TEXT_COLOR, font=font)

    final = Image.new("RGB", (grid_pixels + args.legend_width, grid_pixels))
    final.paste(collage, (0, 0))
    final.paste(legend, (grid_pixels, 0))
    return final


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description="Generate a colour-sorted album-art collage.")
    p.add_argument("--input", default=INPUT_FILE, help="input JSON (default: %(default)s)")
    p.add_argument("--output", default=OUTPUT_FILE, help="output image (default: %(default)s)")
    p.add_argument("--cache-dir", default=CACHE_DIR, help="image cache folder (default: %(default)s)")
    p.add_argument("--grid-size", type=int, default=GRID_SIZE)
    p.add_argument("--cell-size", type=int, default=CELL_SIZE)
    p.add_argument("--legend-width", type=int, default=LEGEND_WIDTH)
    p.add_argument("--sort-by", choices=["hue", "brightness"], default=SORT_BY)
    p.add_argument("--fill-order", choices=["diagonal", "horizontal"], default=FILL_ORDER)
    p.add_argument("--no-rainbow", action="store_true", default=not RAINBOW_SORT,
                   help="keep original rank order")
    p.add_argument("--grey-threshold", type=float, default=GREY_THRESHOLD)
    p.add_argument("-y", "--yes", action="store_true",
                   help="continue without asking if some images are missing")
    args = p.parse_args()
    if args.grid_size < 1 or args.cell_size < 1:
        p.error("--grid-size and --cell-size must be positive")
    return args


def main():
    safe_console()
    args = parse_args()
    input_file, output_file = resolve(args.input), resolve(args.output)
    cache_dir = resolve(args.cache_dir)

    print(f"Reading {input_file}...")
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        sys.exit(f"CRITICAL ERROR: {input_file} not found.")
    except json.JSONDecodeError as e:
        sys.exit(f"CRITICAL ERROR: {input_file} is not valid JSON: {e}")
    if not isinstance(data, list):
        sys.exit(f"CRITICAL ERROR: {input_file} must contain a JSON array.")

    target = args.grid_size * args.grid_size
    print(f"--- CHECKING / DOWNLOADING IMAGES ({len(data)} items in pool) ---")
    entries, failed = fetch_all(data, cache_dir)

    if failed:
        print("\n" + "=" * 40)
        print(f"NOTE: {len(failed)} images failed to download.")
        print("=" * 40)
        for rank, artist, reason in failed:
            print(f"Rank {rank} ({artist}): {reason}")

    if not entries:
        sys.exit("No images available - nothing to render.")

    if len(entries) < target:
        print("\n" + "=" * 40)
        print(f"WARNING: Only {len(entries)} / {target} images are available.")
        print("=" * 40)
        if not args.yes:
            if not sys.stdin.isatty():
                sys.exit("Not enough images. Re-run with --yes to render anyway.")
            answer = input("\nContinue anyway? (Remaining slots will be black) [y/N]: ")
            if answer.strip().lower() != "y":
                sys.exit("Script stopped. Check URLs or connection and run again.")

    if args.no_rainbow:
        chosen = entries[:target]
    else:
        ordered = sort_by_color(entries, args.sort_by, args.grey_threshold)
        if len(ordered) > target:
            # Sample evenly across the spectrum so the gradient stays complete.
            print(f"\nSub-sampling {target} of {len(ordered)} images across the gradient...")
            step = len(ordered) / target
            chosen = [ordered[int(i * step)] for i in range(target)]
        else:
            chosen = ordered

    final = render(chosen, args)
    print(f"Saving to {output_file}...")
    final.save(output_file, quality=90)
    print("Success! Open the file to view results.")


if __name__ == "__main__":
    main()
