"""
Normalise genres and tags in every metadata shard (see genres.py for the rules).

Usage:
  python normalize_shard_genres.py            # dry run: print a summary
  python normalize_shard_genres.py --apply    # rewrite the shards
"""

import copy
import glob
import json
import os
import sys
from collections import Counter

from genres import normalise_artist

SHARDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "metadata", "shards")


def main():
    apply = "--apply" in sys.argv
    before, after = Counter(), Counter()
    changed_artists = changed_shards = 0

    for path in sorted(glob.glob(os.path.join(SHARDS_DIR, "base-*.json"))):
        with open(path, "r", encoding="utf-8", newline="") as f:
            raw = f.read()
        shard = json.loads(raw)
        modified = False
        for artist in shard:
            original = copy.deepcopy(artist)
            normalise_artist(artist)
            for al in original.get("albums", []):
                before.update(al.get("genres", []))
            for al in artist.get("albums", []):
                after.update(al.get("genres", []))
            if artist != original:
                changed_artists += 1
                modified = True
        if modified:
            changed_shards += 1
            if apply:
                out = json.dumps(shard, indent=2, ensure_ascii=False)
                if "\r\n" in raw:
                    out = out.replace("\n", "\r\n")
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(out)

    print(f"{'Updated' if apply else 'Would update'} {changed_artists} artists in {changed_shards} shards.")
    print(f"Distinct album genres: {len(before)} -> {len(after)}")


if __name__ == "__main__":
    main()
