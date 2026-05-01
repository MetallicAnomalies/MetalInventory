// migrate.ts
import { createHash } from 'node:crypto';
import { writeFileSync, readFileSync, readdirSync, mkdirSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';

// ─── Genre / style normalisation ─────────────────────────────────────────────
// Mirrors _SHORTHAND_METAL in enrich.py — keep both in sync.

const SHORTHAND_METAL: Record<string, string> = {
    // Core / Traditional
    'heavy': 'Heavy Metal',
    'speed': 'Speed Metal',
    'thrash': 'Thrash Metal',
    'death': 'Death Metal',
    'black': 'Black Metal',
    'doom': 'Doom Metal',
    'power': 'Power Metal',
    'glam': 'Glam Metal',
    'hair': 'Glam Metal',

    // Doom variants
    'sludge': 'Sludge Metal',
    'stoner': 'Stoner Metal',
    'funeral doom': 'Funeral Doom Metal',
    'death doom': 'Death Doom Metal',
    'gothic doom': 'Gothic Doom Metal',
    'drone doom': 'Drone Doom Metal',
    'epic doom': 'Epic Doom Metal',

    // Death variants
    'melodic death': 'Melodic Death Metal',
    'melodeath': 'Melodic Death Metal',
    'brutal death': 'Brutal Death Metal',
    'technical death': 'Technical Death Metal',
    'tech death': 'Technical Death Metal',
    'tech-death': 'Technical Death Metal',
    'progressive death': 'Progressive Death Metal',
    'slam death': 'Slam Death Metal',
    'slam': 'Slam Death Metal',
    'old school death': 'Old School Death Metal',
    'ossdm': 'Old School Death Metal',

    // Black variants
    'raw black': 'Raw Black Metal',
    'atmospheric black': 'Atmospheric Black Metal',
    'atmo black': 'Atmospheric Black Metal',
    'symphonic black': 'Symphonic Black Metal',
    'melodic black': 'Melodic Black Metal',
    'post-black': 'Post-Black Metal',
    'post black': 'Post-Black Metal',
    'depressive black': 'Depressive Black Metal',
    'dsbm': 'Depressive Black Metal',
    'ambient black': 'Ambient Black Metal',
    'pagan black': 'Pagan Black Metal',
    'war black': 'War Black Metal',
    'bestial black': 'Bestial Black Metal',
    'blackgaze': 'Blackgaze',

    // Blackened crossovers
    'blackened': 'Blackened Metal',
    'blackened death': 'Blackened Death Metal',
    'blackened thrash': 'Blackened Thrash Metal',
    'blackened doom': 'Blackened Doom Metal',

    // Thrash variants
    'groove': 'Groove Metal',
    'crossover thrash': 'Crossover Thrash',
    'crossover': 'Crossover Thrash',
    'teutonic thrash': 'Teutonic Thrash Metal',
    'bay area thrash': 'Bay Area Thrash Metal',

    // Progressive / Avant-garde
    'prog': 'Progressive Metal',
    'progressive': 'Progressive Metal',
    'prog death': 'Progressive Death Metal',
    'extreme progressive': 'Extreme Progressive Metal',
    'avantgarde': 'Avantgarde Metal',
    'avant-garde': 'Avantgarde Metal',
    'avant garde': 'Avantgarde Metal',
    'experimental': 'Experimental Metal',
    'math metal': 'Math Metal',
    'math': 'Math Metal',
    'djent': 'Djent',
    'progressive power metal': 'Progressive Power Metal',

    // Atmospheric / Post
    'post-metal': 'Post-Metal',
    'post metal': 'Post-Metal',
    'atmospheric': 'Atmospheric Metal',
    'ambient': 'Ambient Metal',

    // Symphonic / Gothic / Orchestral
    'symphonic': 'Symphonic Metal',
    'gothic': 'Gothic Metal',
    'orchestral': 'Orchestral Metal',
    'neoclassical': 'Neoclassical Metal',
    'opera metal': 'Opera Metal',
    'operatic': 'Operatic Metal',

    // Folk / Viking / Pagan
    'folk': 'Folk Metal',
    'viking': 'Viking Metal',
    'pagan': 'Pagan Metal',
    'celtic': 'Celtic Metal',
    'medieval': 'Medieval Metal',
    'pirate': 'Pirate Metal',

    // Industrial / Electronic
    'industrial': 'Industrial Metal',
    'cyber': 'Cyber Metal',
    'electro': 'Electro Metal',
    'nu': 'Nu Metal',
    'nu-metal': 'Nu Metal',

    // Hardcore crossovers
    'metalcore': 'Metalcore',
    'deathcore': 'Deathcore',
    'grindcore': 'Grindcore',
    'grind': 'Grindcore',
    'powerviolence': 'Powerviolence',
    'mathcore': 'Mathcore',
    'hardcore': 'Hardcore Metal',
    'crust': 'Crust Metal',
    'd-beat': 'D-Beat',

    // Misc / Niche
    'war metal': 'War Metal',
    'speed doom': 'Speed Doom Metal',
    'epic': 'Epic Metal',
    'desert': 'Desert Metal',
    'spaghetti': 'Spaghetti Metal',
    'psych': 'Psychedelic Metal',
    'psychedelic': 'Psychedelic Metal',
    'noise': 'Noise Metal',
    'drone': 'Drone Metal',
    'southern': 'Southern Metal',
    'country': 'Country Metal',
    'rap metal': 'Rap Metal',
    'funk metal': 'Funk Metal',
    'jazz metal': 'Jazz Metal',
    'nitm': 'New Wave of Traditional Heavy Metal',
    'nwothm': 'New Wave of Traditional Heavy Metal',
    'nwobhm': 'New Wave of British Heavy Metal',
    'nwoahm': 'New Wave of American Heavy Metal',
    'extreme avantgarde': 'Extreme Avantgarde Metal',

    // Extreme variants
    "extreme": "Extreme Metal",
    "extreme metal": "Extreme Metal",
    "extreme prog": "Extreme Progressive Metal",
    "extreme progressive death": "Extreme Progressive Death Metal",
    "extreme prog death": "Extreme Progressive Death Metal",
    "extreme death": "Extreme Death Metal",
    "extreme black": "Extreme Black Metal",
    "extreme doom": "Extreme Doom Metal",
    "extreme gothic": "Extreme Gothic Metal",
    "extreme gothic metal": "Extreme Gothic Metal",
    "extreme symphonic": "Extreme Symphonic Metal",
    "extreme thrash": "Extreme Thrash Metal",
    "extreme power": "Extreme Power Metal",
    "extreme folk": "Extreme Folk Metal",
    "extreme industrial": "Extreme Industrial Metal",
};

/**
 * Expand a single genre/tag string using the shorthand map.
 * Returns the canonical form if found, otherwise the original string.
 */
function normaliseGenre(genre: string): string {
    const key = genre.toLowerCase().trim();
    return SHORTHAND_METAL[key] ?? genre;
}

/** Apply normaliseGenre() to every entry in an array, removing duplicates. */
function normaliseGenres(genres: string[]): string[] {
    return [...new Set(genres.map(normaliseGenre))];
}

const SOURCE_DIR = './metadata';
const OUTPUT_DIR = './output/shards';

interface SourceEntry {
    mbid?: string;
    albums: Array<{ name: string; year: string; genres: string[] }>;
    tags: string[];
    origin?: string;
}

interface MetadataShardEntry {
    normalizedName: string;
    displayName: string;
    /** Alternate display names seen in source metadata keys. */
    aliases?: string[];
    mbid?: string;
    albums: Array<{ name: string; year: string; genres: string[] }>;
    tags: string[];
    origin?: string;
}

function normalize(name: string): string {
    // Unicode-aware: keep letters/digits from any script, strip accents/marks and punctuation.
    const normalized = name
        .normalize('NFKD')
        .toLowerCase()
        .replace(/\p{M}/gu, '')
        .replace(/[^\p{L}\p{N}]/gu, '');

    // Avoid collapsing purely-symbol names (e.g. ".", "...?") into the same empty key.
    return normalized.length > 0
        ? normalized
        : createHash('sha256').update(name).digest('hex').slice(0, 16);
}

function shardKey(normalizedName: string): string {
    return createHash('sha256').update(normalizedName).digest('hex').slice(0, 2);
}
const EDITION_STRIP = /(?:\s*[\(\[\-:]\s*|\s+)(deluxe|expanded|anniversary|remaster(?:ed)?|reissue|limited|special|collector'?s?|bonus|super|ultimate|definitive|platinum|gold|edition|version|issue|\d+(?:th|st|nd|rd)\s+anniversary|hd\s+upgrade|4k\s+upgrade|full\s+version)\b.*/i;
const EXCLUDED_RELEASE_PATTERN = /\b(single|live|demo|acoustic|instrumental|unplugged|split|bootleg|compilation|greatest hits|best of|anthology|sampler|interview|remix|remixes|karaoke|a cappella|instrumentals?)\b/i;
const EP_PATTERN = /\bep\b/i;

function normalizeAlbumKey(title: string): string {
    return normalize(title.replace(EDITION_STRIP, '').replace(/^[\s(\[]+/, ''));
}

const DISTINCT_FORMAT_PATTERN = /\b(ep|live|demo|acoustic|instrumental|unplugged|split|bootleg|compilation|single|remix)\b/i;

function isDistinctRelease(albumName: string): boolean {
    return DISTINCT_FORMAT_PATTERN.test(albumName);
}

function isRelevantRelease(albumName: string): boolean {
    return EP_PATTERN.test(albumName) || !EXCLUDED_RELEASE_PATTERN.test(albumName);
}

function albumPreferenceScore(albumName: string): number {
    let score = 0;
    if (!isRelevantRelease(albumName)) score += 10_000;
    if (EDITION_STRIP.test(albumName)) score += 1_000;
    if (EXCLUDED_RELEASE_PATTERN.test(albumName) && !EP_PATTERN.test(albumName)) score += 500;
    score += albumName.length;
    return score;
}

function displayNameScore(name: string): number {
    const chars = [...name];
    const mixedCase = chars.filter(c => c !== c.toLowerCase()).length;
    const nonAscii = chars.filter(c => c.charCodeAt(0) > 0x7f).length;
    const punctuation = chars.filter(c => /[^\p{L}\p{N}\s]/u.test(c)).length;
    return nonAscii * 1000 + punctuation * 100 + mixedCase * 10 + chars.length;
}

function mergeAlbums(albums: MetadataShardEntry['albums']): MetadataShardEntry['albums'] {
    const albumMap = new Map<string, MetadataShardEntry['albums'][0]>();

    for (const album of albums) {
        if (!isRelevantRelease(album.name)) continue;

        const key = isDistinctRelease(album.name)
            ? normalize(album.name)
            : normalizeAlbumKey(album.name);

        if (!albumMap.has(key)) {
            albumMap.set(key, { ...album });
            continue;
        }

        const existing = albumMap.get(key)!;
        if (albumPreferenceScore(album.name) < albumPreferenceScore(existing.name)) {
            existing.name = album.name;
        }
        if (album.year && (!existing.year || album.year < existing.year)) {
            existing.year = album.year;
        }
        existing.genres = [...new Set([...existing.genres, ...album.genres])];
    }

    return [...albumMap.values()].sort((a, b) => {
        const yearCompare = (a.year || '').localeCompare(b.year || '');
        return yearCompare !== 0 ? yearCompare : a.name.localeCompare(b.name);
    });
}

function mergeEntries(group: MetadataShardEntry[]): MetadataShardEntry {
    if (group.length === 1) return { ...group[0], albums: mergeAlbums(group[0].albums) };

    // Canonical display name: prefer the richest spelling (diacritics/punctuation/casing), then length
    const canonical = group.reduce((best, e) => {
        return displayNameScore(e.displayName) >= displayNameScore(best.displayName) ? e : best;
    }, group[0]);

    // Merge albums — deduplicate by normalized album key, preserving distinct releases
    const albumMap = new Map<string, MetadataShardEntry['albums'][0]>();
    for (const entry of group) {
        for (const album of entry.albums) {
            if (!isRelevantRelease(album.name)) continue;
            const key = isDistinctRelease(album.name)
                ? normalize(album.name)
                : normalizeAlbumKey(album.name);
            if (!albumMap.has(key)) {
                albumMap.set(key, { ...album });
            } else {
                const existing = albumMap.get(key)!;
                // Prefer the plainest canonical title over deluxe/remix/live variants.
                if (albumPreferenceScore(album.name) < albumPreferenceScore(existing.name)) {
                    existing.name = album.name;
                }
                // Always keep earliest year
                if (album.year && (!existing.year || album.year < existing.year)) {
                    existing.year = album.year;
                }
                // Union genres
                existing.genres = [...new Set([...existing.genres, ...album.genres])];
            }
        }
    }

    // Merge tags — union, deduplicated
    const tagSet = new Set<string>();
    for (const entry of group) {
        for (const tag of entry.tags) tagSet.add(tag);
    }

    const aliasSet = new Set<string>();
    for (const entry of group) aliasSet.add(entry.displayName);
    aliasSet.delete(canonical.displayName);
    const aliases = [...aliasSet].sort((a, b) => a.localeCompare(b));

    return {
        normalizedName: canonical.normalizedName,
        displayName: canonical.displayName,
        ...(aliases.length > 0 && { aliases }),
        ...(group.find(e => e.mbid) && { mbid: group.find(e => e.mbid)!.mbid }),
        ...(group.find(e => e.origin) && { origin: group.find(e => e.origin)!.origin }),
        albums: mergeAlbums([...albumMap.values()]),
        tags: [...tagSet],
    };
}

// ─── Load ─────────────────────────────────────────────────────────────────────

const raw: MetadataShardEntry[] = [];
const sourceDisplayNames = new Set<string>();
const files = readdirSync(SOURCE_DIR).filter(f => f.endsWith('.json'));

for (const file of files) {
    const source = JSON.parse(readFileSync(join(SOURCE_DIR, file), 'utf-8')) as Record<string, SourceEntry>;
    for (const [displayName, data] of Object.entries(source)) {
        sourceDisplayNames.add(displayName);
        raw.push({
            normalizedName: normalize(displayName),
            displayName,
            ...(data.mbid && { mbid: data.mbid }),
            ...(data.origin && { origin: data.origin }),
            albums: (data.albums || []).map(a => ({
                ...a,
                genres: normaliseGenres(a.genres || []),
            })),
            tags: normaliseGenres(data.tags || []),
        });
    }
}

console.log(`Loaded ${raw.length} raw entries from ${files.length} files`);

// ─── Merge variants ───────────────────────────────────────────────────────────

const grouped = new Map<string, MetadataShardEntry[]>();
for (const entry of raw) {
    const group = grouped.get(entry.normalizedName) ?? [];
    group.push(entry);
    grouped.set(entry.normalizedName, group);
}

const merged: MetadataShardEntry[] = [];
let mergeCount = 0;

for (const [, group] of grouped) {
    if (group.length > 1) {
        console.log(`Merging "${group.map(e => e.displayName).join('" + "')}" → "${mergeEntries(group).displayName}"`);
        mergeCount++;
    }
    merged.push(mergeEntries(group));
}

console.log(`After merge: ${merged.length} artists (collapsed ${mergeCount} duplicate groups)`);

const preservedNames = new Set<string>();
for (const entry of merged) {
    preservedNames.add(entry.displayName);
    for (const alias of entry.aliases ?? []) preservedNames.add(alias);
}

const dropped = [...sourceDisplayNames].filter(n => !preservedNames.has(n));
if (dropped.length > 0) {
    console.error(`ERROR: ${dropped.length} source displayName(s) missing from output displayName+aliases`);
    console.error(dropped.slice(0, 50).map(s => `- ${JSON.stringify(s)}`).join('\n'));
    process.exit(1);
}

// ─── Validate true collisions ─────────────────────────────────────────────────
// At this point every normalizedName is unique — a collision here means
// genuinely different bands sharing a name, which requires mbid on both.

// (No further collision possible — mergeEntries already collapsed all groups.
//  The validation below is a safeguard in case future source data introduces
//  intentional duplicate keys with mbid already set.)

let valid = true;
const finalGroups = new Map<string, MetadataShardEntry[]>();
for (const entry of merged) {
    const group = finalGroups.get(entry.normalizedName) ?? [];
    group.push(entry);
    finalGroups.set(entry.normalizedName, group);
}

for (const [name, group] of finalGroups) {
    if (group.length > 1) {
        const missingMbid = group.filter(e => !e.mbid);
        if (missingMbid.length > 0) {
            console.error(`ERROR: true collision on "${name}" — ${missingMbid.length} entr(ies) missing mbid`);
            valid = false;
        } else {
            console.log(`OK: true collision on "${name}" — all ${group.length} entries have mbid`);
        }
    }
}

if (!valid) {
    console.error('Fix collisions before migrating.');
    process.exit(1);
}

// ─── Bucket + write ───────────────────────────────────────────────────────────

const shards = new Map<string, MetadataShardEntry[]>();
for (const entry of merged) {
    const key = shardKey(entry.normalizedName);
    const bucket = shards.get(key) ?? [];
    bucket.push(entry);
    shards.set(key, bucket);
}

mkdirSync(OUTPUT_DIR, { recursive: true });

for (const file of readdirSync(OUTPUT_DIR)) {
    if (/^base-[0-9a-f]{2}\.json$/i.test(file)) {
        unlinkSync(join(OUTPUT_DIR, file));
    }
}

for (const [key, bucket] of shards) {
    bucket.sort((a, b) => a.normalizedName.localeCompare(b.normalizedName));
    writeFileSync(join(OUTPUT_DIR, `base-${key}.json`), JSON.stringify(bucket, null, 2) + '\n');
}

const manifest = {
    version: 1,
    builtAt: Date.now(),
    shards: Object.fromEntries(
        [...shards.keys()].map(k => [k, {
            base: `shards/base-${k}.json`,
            patch: null,
            etag: null,
        }])
    ),
};

writeFileSync('./output/cache-manifest.json', JSON.stringify(manifest, null, 2) + '\n');

console.log(`\nWritten ${shards.size} shards + cache-manifest.json`);
console.log(`\nPush to R2:\n`);
console.log(`for f in output/shards/*.json; do`);
console.log(`  wrangler r2 object put scrobex/\${f#output/} --file=\$f --remote`);
console.log(`done`);
console.log(`wrangler r2 object put scrobex/cache-manifest.json --file=output/cache-manifest.json --remote`);
