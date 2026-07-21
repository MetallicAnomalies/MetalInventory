# 🎨 Scrobex Collage Generator

Python scripts that turn your Scrobex listening history into a **10×10 image collage** with a numbered sidebar legend. Two variants are available:

| Script | Mode |
|---|---|
| `collage_generator.py` | Standard — images in original rank order |
| `generate_rainbow_collage.py` | Rainbow — images sorted by album-art color into a gradient |

---

## Requirements

### Python ≥ 3.8

### Install dependencies

```bash
pip install Pillow requests
```

| Package | Purpose |
|---|---|
| `Pillow` | Image processing, grid composition, text rendering |
| `requests` | Downloading artwork from URLs |

---

## Input: `collage_metadata.json`

Both scripts read a JSON file that is an **array of objects**, one per artist/album entry. Place the file in the same directory as the script before running.

### Schema

```json
[
  {
    "rank": 1,
    "artist": "Artist Name",
    "album": "Album Title",
    "imageUrl": "https://example.com/cover.jpg"
  },
  ...
]
```

| Field | Type | Required | Description |
|---|---|---|---|
| `rank` | `number` | ✅ | Original chart position (used for filename caching) |
| `artist` | `string` | ✅ | Artist name |
| `album` | `string` | ✅ | Album or release title |
| `imageUrl` | `string` | ✅ | Direct URL to the artwork image |

> **Tip:** Export this file directly from the Scrobex app. The file needs at least **100 entries** to fill the 10×10 grid; extras are fine — the scripts sample down automatically.

---

## Configuration

Edit the constants at the top of either script to change behaviour:

### Both scripts

```python
INPUT_FILE = "collage_metadata.json"   # Path to your input JSON
OUTPUT_FILE = "scrobex_collage.jpg"    # Where to save the final image
CACHE_DIR   = "downloaded_images"      # Local folder for cached artwork

GRID_SIZE   = 10    # Grid dimensions (10 = 10×10 = 100 cells)
CELL_SIZE   = 300   # Pixels per cell → final grid is 3000×3000 px

LEGEND_WIDTH = 900  # Width of the sidebar in pixels
```

### Rainbow script only (`generate_rainbow_collage.py`)

```python
RAINBOW_SORT = True          # True = sort by color; False = keep original rank order
SORT_BY      = "hue"         # "hue"  → full color rainbow gradient
                             # "brightness" → dark-to-light gradient
FILL_ORDER   = "horizontal"  # "horizontal" → row-by-row fill
                             # "diagonal"   → top-left to bottom-right diagonal sweep
```

---

## Usage

### Standard collage

```bash
python collage_generator.py
```

### Rainbow collage

```bash
python generate_rainbow_collage.py
```

Both scripts are interactive only when images are missing — otherwise they run to completion automatically.

---

## How It Works

### Phase 1 — Download & Cache

The script iterates over your JSON entries and downloads each artwork image into `CACHE_DIR/`. Files are named `001_Artist_Name.jpg` so re-runs skip already-downloaded images, saving bandwidth and time.

If a download fails, the entry is skipped and logged. If fewer than 100 images are available you'll be prompted whether to continue (missing slots become black placeholders).

### Phase 1.5 — Rainbow Sort *(rainbow script only)*

After downloading, each image is resized to 50×50 px and its **average color is extracted** using `ImageStat`. Colors are converted to HSV and sorted by:

- **Hue** — produces a classic rainbow spectrum across the grid
- **Brightness** — produces a light-to-dark or dark-to-light gradient

If more than 100 images were downloaded, the script **sub-samples evenly** across the sorted spectrum so the color gradient remains smooth.

### Phase 2 — Build the Grid

Each image is resized to `CELL_SIZE × CELL_SIZE` px and pasted onto a `3000×3000 px` canvas. Fill order is either:

- **Horizontal** — left-to-right, top-to-bottom (row by row)
- **Diagonal** — sweeps diagonally from the top-left corner

### Phase 3 — Sidebar Legend

A `900×3000 px` dark sidebar is generated alongside the grid. Each row in the legend corresponds to the same-numbered cell in the collage:

```
1. Artist Name - Album Title
2. Artist Name - Album Title
...
```

Numbers reflect the **collage position** (1 = top-left cell), not the original chart rank.

### Phase 4 — Save

Grid and legend are joined side-by-side into a single `3900×3000 px` JPEG saved at 90% quality.

---

## Output

```
scrobex_collage.jpg       ← Final image (3900 × 3000 px)
downloaded_images/        ← Cached artwork (safe to delete after generation)
  001_Artist_Name.jpg
  002_Another_Artist.jpg
  ...
```

---

## Tips & Troubleshooting

| Problem | Solution |
|---|---|
| Font looks pixelated / wrong | Script tries `arial.ttf`; if not found it falls back to Pillow's built-in font. Place `arial.ttf` in the same directory to use a better font. |
| Some images are black boxes | The URL failed or returned a non-image. Check the console log for `!!! FAILED` lines. |
| Colors don't form a clean rainbow | Ensure `RAINBOW_SORT = True` and `SORT_BY = "hue"`. Album arts with very low saturation (greyscale) cluster at the end. |
| Want to regenerate without re-downloading | Images are cached in `CACHE_DIR/`. As long as that folder exists, re-runs skip the download phase. |
| Running for albums vs. artists | Change `INPUT_FILE`, `OUTPUT_FILE`, and `CACHE_DIR` to separate filenames (e.g. `collage_metadata_albums.json`) to avoid cache collisions between runs. |
