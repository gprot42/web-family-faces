# Family Faces

Local-first app for a family photo library: detect every face, cluster similar faces, **name a person once**, then match the rest. Built for albums that span a century — the same person at 3 and 75 will usually be two clusters; you merge them.

Grok / SuperGrok Heavy is not the identity engine. Face matching uses InsightFace `buffalo_l` (ArcFace) on your machine. Originals are never moved.

Optional: open **Settings** and paste an xAI key, then click **Look up famous face** on a group or person. That sends only a face crop to Grok (with web search) in case the face is public — it never auto-names, and it never uploads originals. On a photo, **Sharpen** asks Grok Imagine for a temporary crisper preview, and **Change with Grok** applies a prompt (restore colour, black and white, repair scratches) to a temporary preview. The original file is never overwritten. The key is stored in the Family Faces data folder (`data/xai.api_key`) and copied to `~/.config/xai/api_key` (same name as `XAI_API_KEY`), not in the name catalog backup.

## What you get

- Folder import (incremental by path)
- Multi-face detection per photo
- Unknown-cluster inbox (name 80 faces in one action)
- Photo review with keyboard shortcuts
- People timeline + merge for age-split identities
- Dashboard: people named, faces recognised, faces not named, unknown clusters

## Run

Needs Python 3.12 (InsightFace) and Node 20+.

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
./scripts/app.sh debug                      # foreground, API reload, debug logs
./scripts/app.sh start --port 8750 --ui-port 5180
./scripts/app.sh logs --follow
```

`./scripts/dev.sh` is a foreground alias for `./scripts/app.sh start --foreground`.

Click **Choose folders** and open a NAS share (mount it in Finder first), or paste a folder path. Then **Find Known Faces**. The first scan downloads `buffalo_l` (~300MB) into `data/models`.

Originals are never moved, renamed, copied, or rewritten (including EXIF). Names live in `data/photosort.db`, and each album folder also gets a portable `.photosort.json` so a copied folder keeps its labels. Close the app any time; **Continue tagging** resumes the next unnamed group.

## How to work a 6,000-photo library

1. Index the folder (import → detect → cluster → match).
2. Open **Unknown clusters**, largest first, and type a name.
3. On **People**, merge childhood / adult / elder identities when you know they are the same person.
4. Sweep leftover faces in **Photos** (filter “unnamed”). Keys: `1–5` assign suggestion, `n` new person, `u` unassign, `j/k` next face, arrows next photo.

Child↔adult matching is not automatic. That is a model limit, not a missing feature.

## Data

App state lives in `data/` (SQLite, thumbnails, face crops, ONNX models). Originals stay read-only. Each album folder may contain `.photosort.json` (names only) so copies stay labelled.

## Privacy

Keep personal names, photo paths, GEDCOM trees, API keys, and face crops out of git. They belong in `data/` (ignored) or in the album folders on disk. Tests and UI copy use fictional names only. The sample tree in `fixtures/sample-family.ged` is made-up (Smith / Doe), not a real family.
