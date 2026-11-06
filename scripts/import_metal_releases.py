"""
Import metal_releases.json into metadata/shards/base-XX.json.

For every release, finds (or creates) the artist in its shard and adds the
album if it isn't already there. Existing albums that are missing a year or
genres get them filled from the release; existing values are never changed.

Robustness:
  - Album matching uses both the plain normalized title and the edition-aware
    key from enrich.get_album_key, so "X (20th Anniversary)" or "X - Deluxe"
    don't create duplicates of "X".
  - Malformed releases are skipped and counted instead of crashing the run.
  - An unreadable shard is skipped (never overwritten); the run exits 1.
  - Shards are written atomically (temp file + rename).

Usage:
  python scripts/import_metal_releases.py            # apply
  python scripts/import_metal_releases.py --dry-run  # report only
"""

import argparse
import copy
import json
import os
import re
import sys
import tempfile

# Make sibling modules importable regardless of the working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from enrich import normalize, shard_key, get_album_key  # noqa: E402
from genres import normalise_artist  # noqa: E402

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASES_PATH = os.path.join(WORKSPACE, "metal_releases.json")
SHARDS_DIR = os.path.join(WORKSPACE, "metadata", "shards")

_YEAR_RE = re.compile(r"^(\d{4})")


def atomic_write_json(path: str, data, indent: int = 2) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def album_keys(name: str) -> set[str]:
    """All keys under which an album title counts as 'the same album'."""
    return {normalize(name), get_album_key(name)}


def release_year(release: dict) -> str:
    m = _YEAR_RE.match(str(release.get("release_date") or "").strip())
    return m.group(1) if m else ""


def release_genres(release: dict) -> list[str]:
    raw = release.get("genre_list")
    if not isinstance(raw, list) or not raw:
        raw = release.get("genre", "")
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for g in raw:
        g = str(g).strip()
        if g and g.lower() not in seen:
            seen.add(g.lower())
            out.append(g)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Import metal_releases.json into metadata shards")
    parser.add_argument("--dry-run", action="store_true", help="report changes without writing")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    print(f"Loading releases from {RELEASES_PATH}...")
    try:
        with open(RELEASES_PATH, "r", encoding="utf-8") as f:
            releases = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read {RELEASES_PATH}: {exc}")
        return 1
    if not isinstance(releases, list):
        print(f"ERROR: {RELEASES_PATH} is not a JSON array.")
        return 1

    # Group releases by shard key
    shards_to_update: dict[str, list[dict]] = {}
    malformed = 0
    for release in releases:
        if not isinstance(release, dict):
            malformed += 1
            continue
        artist = str(release.get("artist") or "").strip()
        album = str(release.get("album") or "").strip()
        if not artist or not album:
            malformed += 1
            continue
        shards_to_update.setdefault(shard_key(normalize(artist)), []).append(release)

    print(f"Found {len(releases)} releases, spanning {len(shards_to_update)} shards"
          f"{f' ({malformed} malformed skipped)' if malformed else ''}.")

    updated_shards = added_artists = added_albums = filled_albums = 0
    failed_shards: list[str] = []

    for s_key, rels in sorted(shards_to_update.items()):
        shard_path = os.path.join(SHARDS_DIR, f"base-{s_key}.json")
        if os.path.exists(shard_path):
            try:
                with open(shard_path, "r", encoding="utf-8") as f:
                    shard_data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                print(f"ERROR: skipping unreadable shard {shard_path}: {exc}")
                failed_shards.append(s_key)
                continue
            if not isinstance(shard_data, list):
                print(f"ERROR: skipping shard {shard_path}: not a JSON array")
                failed_shards.append(s_key)
                continue
        else:
            shard_data = []

        artist_map = {
            entry["normalizedName"]: entry
            for entry in shard_data
            if isinstance(entry, dict) and entry.get("normalizedName")
        }

        shard_modified = False

        for release in rels:
            artist_name = str(release["artist"]).strip()
            album_name = str(release["album"]).strip()
            norm_name = normalize(artist_name)
            year = release_year(release)
            genres = release_genres(release)

            artist_entry = artist_map.get(norm_name)
            if artist_entry is None:
                artist_entry = {
                    "normalizedName": norm_name,
                    "displayName": artist_name,
                    "albums": [],
                    "tags": [],
                }
                shard_data.append(artist_entry)
                artist_map[norm_name] = artist_entry
                added_artists += 1
                shard_modified = True

            albums = artist_entry.setdefault("albums", [])
            new_keys = album_keys(album_name)
            existing = next(
                (al for al in albums
                 if isinstance(al, dict) and album_keys(str(al.get("name", ""))) & new_keys),
                None,
            )

            if existing is None:
                albums.append({"name": album_name, "year": year, "genres": genres})
                tags = artist_entry.setdefault("tags", [])
                for g in genres:
                    if g not in tags:
                        tags.append(g)
                normalise_artist(artist_entry)
                added_albums += 1
                shard_modified = True
                continue

            # Fill gaps on an existing album; never overwrite curated values.
            # Year only on an exact title match, so a reissue's year can't
            # land on the original album.
            exact = normalize(str(existing.get("name", ""))) == normalize(album_name)
            want_year = bool(year and exact and not str(existing.get("year") or "").strip())
            want_genres = bool(genres and not existing.get("genres"))
            if not (want_year or want_genres):
                continue

            # Apply on a copy and keep it only if something survives
            # normalise_artist (e.g. "Various" is cleaned away); otherwise
            # the shard would be rewritten on every run.
            idx = albums.index(existing)
            candidate = copy.deepcopy(artist_entry)
            cand_album = candidate["albums"][idx]
            if want_year:
                cand_album["year"] = year
            if want_genres:
                cand_album["genres"] = list(genres)
                tags = candidate.setdefault("tags", [])
                for g in genres:
                    if g not in tags:
                        tags.append(g)
            normalise_artist(candidate)
            if (want_year and cand_album.get("year")) or (want_genres and cand_album.get("genres")):
                artist_entry.clear()
                artist_entry.update(candidate)
                filled_albums += 1
                shard_modified = True

        if shard_modified:
            shard_data.sort(key=lambda x: x.get("normalizedName", "") if isinstance(x, dict) else "")
            if not args.dry_run:
                atomic_write_json(shard_path, shard_data, indent=2)
            updated_shards += 1

    prefix = "[dry-run] Would update" if args.dry_run else "Done. Updated"
    print(f"{prefix} {updated_shards} shards.")
    print(f"Added {added_artists} new artists and {added_albums} new albums; "
          f"filled missing year/genres on {filled_albums} existing albums.")
    if failed_shards:
        print(f"ERROR: {len(failed_shards)} shard(s) could not be read: {', '.join(failed_shards)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
