# Family Faces on Windows

Family Faces is developed on macOS, and macOS stays the default. The same code
runs on Windows 10 and 11: the face matching, catalog, and web UI are identical,
and the few places that touch the operating system (network shares, folder
browsing, opening a browser for sign-in) have Windows branches.

## What you need

- **Python 3.12** from python.org. Tick "Add python.exe to PATH" and make sure
  the `py` launcher is installed.
- **Node.js 20 or newer** from nodejs.org.
- **Visual Studio Build Tools** with the "Desktop development with C++"
  workload. InsightFace, the face model, ships no Windows wheel and compiles a
  small extension during `pip install`. Without the build tools that step fails.
- **Git** if you clone rather than download.

## Install

Open PowerShell in the folder you cloned or unzipped, then:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm install
cd ..
```

If `pip install` stops at InsightFace, install the Build Tools above, open a
new PowerShell window, and run the `pip install` line again.

## Run

```powershell
.\scripts\app.cmd start
```

Then open http://127.0.0.1:5174 in your browser. The same launcher offers
`stop`, `restart`, `status`, `debug` (API reloads on code changes), and
`logs api` or `logs ui`. `scripts\app.ps1` is the PowerShell equivalent, and
`python scripts\app.py` works from any shell.

The first start downloads the face models into `data\models`.

Ports and the data folder can be changed with environment variables before
starting: `PHOTOSORT_PORT` (API, default 8741), `PHOTOSORT_UI_PORT` (UI,
default 5174), `PHOTOSORT_DATA` (default `data` in the project).

## Photos on a NAS

Use either form of network path when you choose folders:

- A UNC path such as `\\nas\photos\Family`. Family Faces remembers the share
  and reconnects it with `net use` when needed, using the credential Windows
  has saved for that server. Save the credential once by opening the share in
  File Explorer and ticking "Remember my credentials", or with Credential
  Manager.
- A mapped drive letter such as `Z:\Family`. Mapped drives appear under NAS
  drives in the folder picker.

"Connect NAS" in the folder picker connects the shares your albums live on.
Bonjour discovery of Synology servers is a macOS feature; on Windows the app
uses the servers already seen in your album paths.

## Where things are

| Item | Location |
|---|---|
| Catalog and face data | `data\photosort.db` and `data\crops` |
| Logs | `data\logs\api.log`, `data\logs\ui.log` |
| PID files for the launcher | `data\run` |
| Face models | `data\models` |

Album sidecar files (`.photosort.json`) are written next to your photos as on
macOS. Originals are never modified.

## Differences from macOS

- Shares are reached by `\\server\share` paths instead of `/Volumes/name`.
- Connecting a share uses `net use` instead of Finder, so no Keychain prompt;
  Windows must already hold the credential.
- The SuperGrok sign-in opens Brave, Chrome, or Firefox from their usual
  install folders, and falls back to your default browser. Safari is not
  offered.
- HEIC and RAW photos work through pillow-heif and rawpy, which both ship
  Windows wheels.

## Running the tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q
cd ..\frontend
node --test src\*.test.js
```

The Windows branches are covered on every platform by `backend\tests\test_windows.py`,
which flips the platform flags rather than needing a Windows machine.
