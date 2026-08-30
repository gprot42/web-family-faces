# Family Faces metadata file (`.photosort.json`)

Family Faces never rewrites photo files or their EXIF. Names, notes, tags,
and “not a person” marks live in the app database (`data/photosort.db`)
and, as a portable copy, in a sidecar next to each album:

```text
1994 - Harbor/
  IMG_0042.jpg
  IMG_0043.jpg
  .photosort.json
```

The sidecar is the only file Family Faces is allowed to create, replace,
or delete inside an album folder. Copying the folder (USB, NAS, another
Mac) keeps the labels. A later **Find Known Faces** on the copy restores
them into the catalog.

This document describes version **1** of that file.

## Role versus the catalog

| | App database | `.photosort.json` |
|---|---|---|
| Scope | Whole library; one person across albums | One album folder |
| Identity | Numeric `person_id`, embeddings, clusters | Display **name** only |
| Geometry | Pixel boxes on the indexed file | Width/height fractions |
| Purpose | Live tagging, matching, UI | Portable backup / round-trip |

The catalog wins if a face is already named. Restore only fills
**unnamed** faces (and empty comments/tags). Purging the database does
**not** delete sidecars; a later scan can put names back from them.
Reset matching also leaves sidecars alone.

## File rules

- **Name:** exactly `.photosort.json` (leading dot).
- **Location:** the folder that **directly** contains the photos, not a
  parent of several albums.
- **Encoding:** UTF-8 JSON, 2-space indent, trailing newline.
- **Write:** atomic (`'.photosort.json.tmp'` then replace). Unchanged
  text is not rewritten.
- **When it appears:** only if at least one photo in the folder has
  something to store (a named face, a junk/statue mark, a face note, a
  photo comment, or tags). A brand-new import with no names does not
  create the file.
- **When it is removed:** the album is in the catalog, has faces, and
  none of those photos still have sidecar-worthy data.
- **When it is kept empty:** a copy that still has the file, but faces
  have not been detected yet — so restore can run after the scan.

Hidden / preview copies (`@eaDir`, `1024 x 768`, and similar) are not
indexed and do not get a sidecar.

## Top-level object

```json
{
  "version": 1,
  "app": "family-faces",
  "updated_at": "2026-08-30T12:04:16+00:00",
  "photos": {
    "IMG_0042.jpg": { }
  }
}
```

- **`version`** (number, required): Format version. Current writers emit
  `1`.
- **`app`** (string, required): Always `"family-faces"`.
- **`updated_at`** (string, required): UTC timestamp, ISO-8601, second
  precision (no fractional seconds).
- **`photos`** (object, required): Map of **filename → photo entry**.
  Keys are the file’s basename only (`IMG_0042.jpg`), never a path.

Unknown top-level keys should be ignored by readers. Extra keys on photo
or face objects should be ignored too.

## Looking up a photo

Restore matches an indexed file to a `photos` entry in this order:

1. Exact filename.
2. Case-insensitive filename.
3. Same `sha256` as stored on the entry (file was renamed but not
   edited).

If none match, that photo is skipped.

## Photo entry

A photo is omitted from `photos` unless it has faces worth storing, a
comment, or tags.

```json
"IMG_0042.jpg": {
  "sha256": "a1b2c3…64 hex chars…",
  "comment": "Arthur at the back.",
  "tags": ["Christmas", "family"],
  "faces": [ ]
}
```

- **`sha256`** (string): Present if the catalog has a hash. SHA-256 of
  the original file bytes. Used only as a rename fallback.
- **`comment`** (string): Present if non-empty. Photo note, max 4000
  characters. Restore writes it only if the catalog comment is empty.
- **`tags`** (string[]): Present if non-empty. Photo tags as typed
  (order preserved from the catalog). Restore writes them only if the
  photo has **no** tags yet. On restore each tag is trimmed, collapsed
  whitespace, max 40 characters, max 12 tags, case-insensitive de-dupe.
  `"later review"` is a normal tag.
- **`faces`** (object[]): Present if any labelled face. Faces that have
  a name, a junk mark, and/or a face comment. Unnamed, unremarked
  detections are **not** stored.

## Face object

```json
{
  "box": [0.12, 0.18, 0.31, 0.55],
  "name": "Sam Smith",
  "how": "manual",
  "tag": [0.3, 0.2],
  "comment": "holding the cake"
}
```

Or a statue / painting / object:

```json
{
  "box": [0.4, 0.1, 0.7, 0.9],
  "junk": true,
  "how": "junk"
}
```

- **`box`** (number[4], always): Face rectangle `[x1, y1, x2, y2]` in
  **normalized** image coordinates: `0` is the left/top edge, `1` is the
  right/bottom edge. Values are rounded to 6 decimal places. Origin is
  the stored pixels, not EXIF-rotated display.
- **`name`** (string): Named person, not junk. Display name from the
  catalog (`people.name`). There is no `person_id`. The same spelling
  across albums is the same person on restore (case-insensitive).
- **`junk`** (boolean): Only `true`. Face is not a person (statue,
  painting, object). Hidden from tagging.
- **`how`** (string): Present if the catalog set `assigned_how`. How the
  name or junk mark was applied. Written for humans and future tools.
  **Restore does not use this field**; names applied from the sidecar
  are stored as `assigned_how = 'sidecar'`.
- **`tag`** (number[2]): Present if the name chip was dragged. `[left,
  top]` of the name label, normalized `0–1` (5 decimal places). Restore
  converts to percent (`0–100`) and applies only if the face has no pin
  yet.
- **`comment`** (string): Present if non-empty. Note on this face, max
  4000 characters. Restore writes it only if the catalog face comment is
  empty.

A face is stored if it is junk, has a name, or has a face comment.
`box` is required; a face without a usable box is dropped.

Writers accept pixel boxes (values whose absolute size is greater than
`1.5`) and divide by the photo’s width and height. Readers do the same,
so either unit is accepted on input.

## Matching faces on restore

Restore does **not** store face ids. It pairs catalog detections with
sidecar faces by overlap:

1. Convert both boxes to normalized coordinates.
2. Score every pair with intersection-over-union (IoU).
3. Greedy match, highest IoU first. Minimum IoU is **0.25**. Each face
   is used at most once.
4. If nothing matched and there is exactly one detection and one sidecar
   face, pair them anyway.

Then, for each pair:

- Apply `tag` if the catalog face has no pin.
- Apply `comment` if the catalog face comment is empty.
- If the face already has a `person_id`, **stop** (catalog wins).
- If `assigned_how` is `'cleared'`, **stop** (user removed the name).
- If `junk` is true, mark the face unidentifiable
  (`assigned_how = 'junk'`).
- Else if `name` is set, find or create a person with that name
  (case-insensitive) and assign `assigned_how = 'sidecar'`.

Unknown people you saved as “Unknown name of person …” are stored as
that name string, like any other name.

## Typical `how` values (informational)

These are catalog values copied into the sidecar. Restore always uses
`'sidecar'` for names it applies.

| Value | Meaning |
|---|---|
| `manual` | You typed or picked the name. |
| `auto` | Matcher applied a catalog name. |
| `cluster` | Named with a cluster. |
| `merge` | People were merged. |
| `sidecar` | Restored from this file. |
| `junk` | Not a person. |
| `cleared` | Name taken off; restore will not put it back on that face. |

## What is not in the file

By design the sidecar is **names and notes**, not the live catalog:

- No embeddings, cluster ids, or InsightFace / AdaFace scores
- No `person_id`, family/work/other flags, or GEDCOM links
- No thumbnails, crops, or Grok Imagine / Sharpen previews
- No photo rotation, hidden flag, or EXIF
- No unnamed face boxes (except junk or a face with a comment)
- No album-level settings (theme, label size, layout)

Those stay in `data/` on the machine that ran Family Faces.

## Example

```json
{
  "version": 1,
  "app": "family-faces",
  "updated_at": "2026-08-30T12:04:16+00:00",
  "photos": {
    "aisle.jpg": {
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "comment": "Arthur at the back.",
      "tags": ["Christmas", "family"],
      "faces": [
        {
          "box": [0.1, 0.1, 0.525, 0.6],
          "name": "Arthur",
          "how": "manual",
          "tag": [0.3, 0.2],
          "comment": "holding the cake"
        }
      ]
    },
    "bronze.jpg": {
      "faces": [
        {
          "box": [0.0625, 0.0625, 0.375, 0.375],
          "junk": true,
          "how": "junk"
        }
      ]
    }
  }
}
```

## Compatibility

- **Version:** readers should accept `version` `1`. A higher version
  means new fields may appear; ignore unknowns rather than failing.
- **Editors:** UTF-8, valid JSON, keep `box` as four numbers. Do not
  pretty-print in a way that changes string values.
- **Privacy:** the file contains people’s names and may contain
  comments. Treat it like the photos. It is not a substitute for the
  SQLite catalog backup under `data/backups/`.

## Source of truth in the app

Writers and readers live in `backend/photosort/sidecar.py`. Album write
permission is enforced in `backend/photosort/originals.py`
(`SIDECAR_NAME`, `assert_sidecar_write`). Tests for round-trip behaviour
are in `backend/tests/test_sidecar.py`.
