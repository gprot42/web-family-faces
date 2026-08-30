# Family Faces

A local app for a family photo library. It finds faces, groups people
who look the same, and lets you **name someone once** — then matches
the rest. Original photos are never moved or rewritten so this can work
with .jpg, .gif, .heic, .raw or any filetype as the metadata describes
the people tagged in each folder.

The same person at 3 and 75 will usually be two groups. Merge them when
you know they are the same person.

![Labeled photos in Later review](docs/example-labeled-photos.jpg)

## Features

**Name faces**
- Import folders from this Mac or a NAS; later runs only look at new
  photos
- Find every face in a photo, including group shots
- Name a whole group of lookalikes in one go
- Match remaining faces to people you have already named
- Review names the app applied on its own

**Browse the library**
- Albums, or view by person or by tag
- Search by name, nickname, or a snapshot
- Keyboard shortcuts for reviewing photos
- Slideshow, name labels on the picture, download with or without labels

**People and family**
- People catalog — merge childhood and later-life photos, add
  nicknames
- Family tree from a GEDCOM file
- Dashboard of people named, faces recognised, and groups still to name

**Originals stay put**
- Photos are never moved, renamed, or rewritten
- Names live in the app; a copied folder keeps its labels
- Close any time and continue tagging later

## Run

Needs Python 3.12 and Node 20+.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt
cd frontend && npm install && cd ..
chmod +x scripts/app.sh scripts/dev.sh
./scripts/app.sh start
```

Open http://127.0.0.1:5174

```bash
./scripts/app.sh stop
./scripts/app.sh status
./scripts/app.sh logs --follow
```

Click **Choose folders**, pick a folder or a NAS share (mount it in
Finder first), then **Find Known Faces**. The first scan downloads the
face models (~300MB).

## Using it

1. Index a folder.
2. Open **Clusters to name**, largest first, and type a name.
3. On **People**, merge childhood / adult / elder cards when they are
   the same person.
4. Sweep leftovers in **Photos** (filter “unnamed”). Keys: `1–5`
   assign a suggestion, `n` new person, `u` unassign, `j`/`k` next
   face, arrows next photo.

Child and adult photos of the same person are not matched automatically.
That is a model limit, not a missing feature.

## Stack

**UI.** React, React Router, and Vite.

**API.** FastAPI, uvicorn, and python-multipart.

**Faces.** InsightFace (`buffalo_l` / ArcFace) and ONNX Runtime run on
this Mac. AdaFace retries a crop when that match is unsure.

**Photos.** Pillow, pillow-heif, rawpy, OpenCV, and NumPy. Originals
photos are never rewritten.

**Tests.** pytest and httpx.

## Privacy

Keep personal names, photo paths, family trees, and face crops out of
git. They belong in `data/` (ignored) or in the album folders on disk.
Tests and UI copy use fictional names only.
