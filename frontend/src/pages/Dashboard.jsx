import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import FolderPicker from "../components/FolderPicker.jsx";
import { addedFolderPaths, folderLabel, importFoldersStored, isAlbumPath, normalizeFolderPath, readImportFolders, writeImportFolders } from "../folders.js";
import { tip } from "../tip.js";
import JobGauge from "../components/JobGauge.jsx";

function pct(n) {
  return `${Math.round((n || 0) * 100)}%`;
}

function num(n) {
  return Number(n || 0).toLocaleString();
}

function fmtBytes(n) {
  const bytes = Number(n) || 0;
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function Dashboard({ stats, jobs: jobsProp, onJobs, onChange }) {
  const nav = useNavigate();
  const [folders, setFolders] = useState(() => readImportFolders(stats?.folder || ""));
  const [typed, setTyped] = useState("");
  const [jobsLocal, setJobsLocal] = useState(null);
  const jobs = jobsProp || jobsLocal;
  const [err, setErr] = useState("");
  const [picker, setPicker] = useState(false);
  const [backupMsg, setBackupMsg] = useState("");
  const [backupBusy, setBackupBusy] = useState(false);
  const [backupDone, setBackupDone] = useState(false);
  const foldersAtPickerOpen = useRef(folders);

  useEffect(() => {
    if (folders.length) return;
    if (stats?.folder) setFolders([stats.folder]);
  }, [stats, folders.length]);

  useEffect(() => {
    if (!folders.length) return;
    try {
      writeImportFolders(folders);
    } catch {
      /* ignore */
    }
  }, [folders]);

  useEffect(() => {
    if (jobsProp !== undefined) return undefined;
    let cancel = false;
    async function tick() {
      try {
        const data = await api.jobs();
        if (!cancel) setJobsLocal(data);
      } catch {
        /* backend may be starting */
      }
    }
    tick();
    const id = setInterval(tick, 8000);
    return () => {
      cancel = true;
      clearInterval(id);
    };
  }, [jobsProp]);

  const active = jobs?.active;
  const latest = jobs?.recent?.[0];
  const paused = !active && latest?.status === "paused" ? latest : null;
  const failed = !active && latest?.status === "error" ? latest : null;
  const failedRaw = failed?.message || "";
  const failedMessage = /UNIQUE constraint failed: photos\.path/i.test(failedRaw)
    ? "A photo was already in the catalog. Already indexed photos are kept."
    : /Unsupported file format or not RAW/i.test(failedRaw)
      ? "A camera-raw file could not be read. That file was skipped; already indexed photos are kept."
      : failedRaw;
  const jobTitle = {
    pipeline: "Finding known faces",
    import: "Reading folder",
    scan: "Finding known faces",
    cluster: "Grouping faces",
    match: "Applying names",
    identify: "Identifying faces",
    verify: "Checking photos",
  };

  function removeFolder(path) {
    setFolders((cur) => {
      const next = cur.filter((item) => normalizeFolderPath(item) !== normalizeFolderPath(path));
      writeImportFolders(next);
      return next;
    });
  }

  function addTypedPath() {
    const path = typed.trim();
    if (!path) return folders;
    if (folders.includes(path)) {
      setTyped("");
      return folders;
    }
    const next = [...folders, path];
    setFolders(next);
    setTyped("");
    return next;
  }

  async function backUpNames() {
    setBackupBusy(true);
    setBackupMsg("");
    try {
      const result = await api.backup();
      const where = result.path || "the backups folder";
      const size = result.bytes ? ` · ${fmtBytes(result.bytes)}` : "";
      setBackupDone(true);
      setBackupMsg(`Name catalog backed up to ${where}${size}. Photo files were not copied.`);
      onChange?.();
    } catch (ex) {
      setBackupDone(false);
      setBackupMsg(ex.message || "Could not back up the name catalog.");
    } finally {
      setBackupBusy(false);
    }
  }

  async function startFindFaces(paths) {
    const list = (paths || []).filter(isAlbumPath);
    if (!list.length) {
      setErr("Choose at least one folder.");
      return;
    }
    setErr("");
    try {
      await api.pipeline(list);
      onChange?.();
    } catch (ex) {
      setErr(ex.message);
    }
  }

  function applyFolders(next, { scanNew = false } = {}) {
    const clean = [...new Set((next || []).filter(isAlbumPath))];
    const added = addedFolderPaths(clean, foldersAtPickerOpen.current);
    setFolders(clean);
    writeImportFolders(clean);
    if (scanNew && added.length && !active) startFindFaces(added);
  }

  async function runPipeline(e) {
    e.preventDefault();
    const next = addTypedPath();
    await startFindFaces(next);
  }

  async function refreshJobs() {
    onChange?.();
    try {
      onJobs?.(await api.jobs());
    } catch {
      /* jobs poll will catch up */
    }
  }

  async function resumeLatest() {
    setErr("");
    try {
      await api.resumeJob();
      await refreshJobs();
    } catch (ex) {
      setErr(ex.message || "Could not resume.");
    }
  }

  const scanFailed = Boolean(failed && ["pipeline", "import", "scan"].includes(failed.type));
  const pendingScan = Math.max(0, (stats?.photos || 0) - (stats?.photos_scanned || 0));

  return (
    <div className="summary-page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Summary</p>
          <h1>Who is in this folder</h1>
          <p className="lede">
            Choose albums, find known faces, then name each group. Photos stay where they are.
          </p>
        </div>
      </div>

      <form onSubmit={runPipeline}>
        {folders.length ? (
          <div className="picked-folders" aria-label="Selected folders">
            {folders.map((path) => (
              <div key={path} className="picked-chip picked-folder">
                <div className="picked-folder-text">
                  <span className="picked-folder-name">{folderLabel(path)}</span>
                  <span className="picked-folder-path">{path}</span>
                </div>
                <button
                  type="button"
                  className="picked-folder-remove"
                  aria-label={`Remove ${folderLabel(path)}`}
                  onClick={() => removeFolder(path)}
                  {...tip(`${path}. Hide from this list. Names, the database, and .photosort.json stay.`)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        ) : (
          <p className="hint" style={{ margin: "0 0 8px" }}>
            Nothing selected yet. Choose folders — new albums start Find Known Faces on their own.
            {stats?.folder ? (
              <>
                {" "}
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    if (!isAlbumPath(stats.folder)) return;
                    foldersAtPickerOpen.current = folders;
                    applyFolders([stats.folder], { scanNew: true });
                  }}
                  {...tip("Put the last scanned album back on this list.")}
                >
                  Use {folderLabel(stats.folder)}
                </button>
              </>
            ) : null}
          </p>
        )}
        <div className="row">
          <input
            className="grow"
            type="text"
            aria-label={folders.length ? "Add another folder path" : "Folder path"}
            placeholder={folders.length ? "Add another folder path" : "Paste a folder path"}
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
          />
          <button
            type="button"
            className="secondary"
            onClick={() => {
              foldersAtPickerOpen.current = folders;
              setPicker(true);
            }}
            {...tip("Open a list of drives and folders. New albums start Find Known Faces when you are done.")}
          >
            Choose folders
          </button>
          <button
            type="submit"
            className="secondary"
            disabled={(!folders.length && !typed.trim()) || !!active || !!paused}
            {...tip(
              scanFailed
                ? "Continue from photos already in the catalog. Already indexed files are not read again."
                : "Read every photo in the selected folders, find known faces, and group people who look the same. Nothing is written back to the photos.",
            )}
          >
            {scanFailed ? "Resume Find Known Faces" : "Find Known Faces"}
          </button>
        </div>
      </form>
      {picker ? (
        <FolderPicker
          selected={folders}
          startPath={stats?.folder || ""}
          onClose={() => setPicker(false)}
          onSelect={(paths, opts = {}) => {
            applyFolders(Array.isArray(paths) ? paths : [paths], { scanNew: !!opts.scan });
          }}
        />
      ) : null}
      {err ? <p className="error">{err}</p> : null}

      {active ? <JobGauge job={active} title={jobTitle[active.type] || active.type} onResumed={refreshJobs} /> : null}
      {paused ? <JobGauge job={paused} title={jobTitle[paused.type] || paused.type} onResumed={refreshJobs} /> : null}
      {failed ? (
        <p className="error">
          {jobTitle[failed.type] || failed.type} stopped: {failedMessage}{" "}
          <button
            type="button"
            className="secondary"
            onClick={resumeLatest}
            {...tip(
              failed.type === "identify"
                ? "Continue matching unnamed groups to the catalog, then look the rest up with Grok."
                : "Continue from photos already in the catalog. Already indexed files are not read again.",
            )}
          >
            Resume
          </button>
        </p>
      ) : null}

      <div className="stats">
        <div
          className="card stat"
          {...tip(
            "How many people you have identified and stored in Faces in DB View. You typed a name, confirmed a lookup, or chose Unknown name of person. All of them are in the local database, not written onto the photos. Named vs still unnamed is only whether they have a real name yet.",
          )}
        >
          <div className="k">People in the database</div>
          <div className="v">{stats ? num(stats.people) : "—"}</div>
          <div className="s">
            {stats
              ? `${num(stats.people_named)} named · ${num(stats.people_unknown)} still unnamed`
              : "identified by you and stored locally"}
          </div>
        </div>
        <div
          className="card stat"
          {...tip(
            "Faces that already have a person’s name. The percent skips blurry faces and statues. Automatic names wait on Check names until you keep or reject them.",
          )}
        >
          <div className="k">Named faces</div>
          <div className="v">{stats ? num(stats.faces_identified) : "—"}</div>
          <div className="s">
            {pct(stats?.identification_rate)} of faces we can name
            {stats?.faces ? <><br />{num(stats.faces)} found in total</> : null}
            {stats?.faces_auto ? <><br />{num(stats.faces_auto)} still on Check names</> : null}
          </div>
        </div>
        <div
          className="card stat"
          {...tip(
            "People marked Unknown, plus faces waiting on the To name page.",
          )}
        >
          <div className="k">Still to name</div>
          <div className="v">
            {stats ? num((stats.people_unknown ?? 0) + (stats.faces_unknown ?? 0)) : "—"}
          </div>
          <div className="s">
            {`${num(stats?.people_unknown)} people without a name`}
            {stats?.faces_unknown ? <><br />{num(stats.faces_unknown)} faces on To name</> : null}
          </div>
        </div>
        <div
          className="card stat"
          {...tip(
            "Photos already in the catalog. Not searched yet means Find Known Faces has not looked for faces in those files. Blurry faces and statues are counted after a photo is searched.",
          )}
        >
          <div className="k">Photos</div>
          <div className="v">{stats?.photos ?? "—"}</div>
          <div className="s">
            {pendingScan > 0
              ? `${pendingScan.toLocaleString()} not searched for faces yet`
              : `${stats?.photos_with_faces ?? 0} with faces`}
          </div>
        </div>
      </div>

      <div className="summary-actions">
        <button
          type="button"
          className="secondary"
          onClick={() => nav("/to-name")}
          {...tip("Groups of unnamed faces. Name a group once instead of photo by photo.")}
        >
          To name
          {(stats?.unknown_clusters || stats?.faces_unknown)
            ? ` · ${stats.unknown_clusters || stats.faces_unknown}`
            : ""}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => nav("/review")}
          {...tip("Faces the matcher named. Keep the right ones. Reject the rest.")}
        >
          Check names
          {stats?.faces_auto ? ` · ${stats.faces_auto}` : ""}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => nav("/people")}
          {...tip("People you have identified, stored in the local database.")}
        >
          Faces in DB View
        </button>
      </div>

      <details className="more">
        <summary>More</summary>
        <div className="row" style={{ marginTop: 10 }}>
          <button
            type="button"
            className="secondary"
            disabled={!!active || !stats?.photos}
            onClick={() => api.verify().then(onChange)}
            {...tip("Re-check the original files. Reports if any photo was changed, moved, or is missing. Does not alter the photos.")}
          >
            Check photos unchanged
          </button>
          <button
            type="button"
            className={backupDone ? "picked" : "secondary"}
            aria-pressed={backupDone}
            disabled={backupBusy}
            onClick={backUpNames}
            {...tip("Back up the name catalog now (gzip). The app also backs up automatically while it is open. Photo files are not copied.")}
          >
            {backupBusy ? "Backing up…" : backupDone ? "Names backed up" : "Back up names"}
          </button>
        </div>
        {backupMsg ? (
          <p className={backupDone ? "save-note backup-status" : "error"} role="status">
            {backupMsg}
          </p>
        ) : null}
        {stats?.backup?.latest ? (
          <p className="hint">
            Latest backup: {stats.backup.latest.name}
            {stats.backup.latest.compressed ? " · gzip" : ""}
            {stats.backup.latest.bytes ? ` · ${fmtBytes(stats.backup.latest.bytes)}` : ""}. Stored in{" "}
            <code>{stats.backup.dir}</code>. The last {stats.backup.keep} backups are kept.
          </p>
        ) : (
          <p className="hint">
            Back up names saves a gzip of the name catalog in the app data folder. Photo files on the
            NAS are not copied — back those up separately.
          </p>
        )}
        {folders.length ? (
          <p className="hint">
            Working folders:{" "}
            {folders.map((path, i) => (
              <span key={path}>
                {i ? ", " : ""}
                <code>{path}</code>
              </span>
            ))}
          </p>
        ) : stats?.folder ? (
          <p className="hint">
            Catalog still has photos from <code>{stats.folder}</code>.
          </p>
        ) : null}
        <p className="hint">
          <Link to="/about">About</Link> — originals stay in place, names travel with the folder.
          {stats?.integrity
            ? ` Originals: ${stats.integrity.ok} unchanged${
                stats.integrity.changed ? `, ${stats.integrity.changed} changed` : ""
              }${stats.integrity.missing ? `, ${stats.integrity.missing} missing` : ""}.`
            : ""}
        </p>
      </details>
    </div>
  );
}
