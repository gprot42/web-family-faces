import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import {
  folderLabel,
  folderSelectionState,
  isAlbumPath,
  isSkipped,
  normalizeFolderPath,
  toggleFolderTick,
} from "../folders.js";
import { tip } from "../tip.js";

function isVolumesPath(path) {
  if (!path) return false;
  const raw = String(path).replace(/\/+$/, "");
  return raw.toLowerCase() === "volumes" || raw.toLowerCase() === "/volumes";
}

function isVolumeRoot(path) {
  const bits = String(path || "").split("/").filter(Boolean);
  return bits.length === 2 && bits[0].toLowerCase() === "volumes";
}

function crumbParts(path) {
  if (isVolumesPath(path)) return [{ label: "NAS drives", path: "volumes" }];
  const bits = String(path || "").split("/").filter(Boolean);
  const nodes = [];
  let soFar = "";
  for (const part of bits) {
    soFar = soFar ? `${soFar}/${part}` : `/${part}`;
    nodes.push({ label: part, path: soFar });
  }
  return nodes;
}

function FolderCheck({ checked, partial, disabled, name, onChange, tipProps }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = !!partial && !checked;
  }, [partial, checked]);
  return (
    <label className="folder-check" {...tipProps}>
      <input
        ref={ref}
        type="checkbox"
        checked={!!checked}
        disabled={disabled}
        aria-checked={partial && !checked ? "mixed" : checked ? "true" : "false"}
        onChange={onChange}
      />
      <span className="visually-hidden">Select {name}</span>
    </label>
  );
}

export default function FolderPicker({ onSelect, onClose, selected = [], excluded: excludedProp = [], startPath = "" }) {
  const [listing, setListing] = useState(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [mounting, setMounting] = useState(false);
  const [mountNote, setMountNote] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  const pathRef = useRef(null);
  const [picked, setPicked] = useState(() => [...new Set((selected || []).filter(isAlbumPath))]);
  const [excluded, setExcluded] = useState(() => [...new Set((excludedProp || []).filter(isAlbumPath))]);
  const excludedRef = useRef(excluded);
  excludedRef.current = excluded;

  function commit(next, opts = {}) {
    const clean = [...new Set((next || []).filter(isAlbumPath))];
    const skip = [...new Set((opts.excluded ?? excluded).filter(isAlbumPath))];
    setPicked(clean);
    setExcluded(skip);
    onSelect(clean, { ...opts, excluded: skip });
    return clean;
  }

  function finish() {
    setPicked((cur) => {
      const clean = [...new Set((cur || []).filter(isAlbumPath))];
      const skip = [...new Set((excludedRef.current || []).filter(isAlbumPath))];
      onSelect(clean, { scan: true, excluded: skip });
      return clean;
    });
    onClose();
  }

  function tickPath(path) {
    const next = toggleFolderTick(path, picked, excluded);
    commit(next.picked, { scan: false, excluded: next.excluded });
  }

  function includeLeaf(listing) {
    const path = listing?.path;
    if (!path || isVolumesPath(path) || !isAlbumPath(path)) return;
    if ((listing.entries || []).length > 0) return;
    const n = normalizeFolderPath(path);
    setPicked((cur) => {
      if (cur.some((item) => normalizeFolderPath(item) === n)) return cur;
      const next = [...cur, path];
      onSelect(next, { scan: false });
      return next;
    });
  }

  async function go(path, { quiet = false } = {}) {
    if (!quiet) {
      setLoading(true);
      setErr("");
      setListing((cur) => (cur ? { ...cur, entries: [] } : cur));
    }
    try {
      const nextListing = await api.browse(path);
      pathRef.current = nextListing?.path ?? path ?? null;
      setListing(nextListing);
      setActive(0);
      includeLeaf(nextListing);
      if (quiet) setErr("");
      const kids = nextListing.entries || [];
      if (
        !quiet &&
        kids.length === 1 &&
        (isVolumesPath(nextListing.path) || !nextListing.path || isVolumeRoot(nextListing.path))
      ) {
        await go(kids[0].path);
        return;
      }
    } catch (ex) {
      if (!quiet) setErr(ex.message);
    } finally {
      if (!quiet) setLoading(false);
    }
  }

  const current = listing?.path;
  const currentSel = current ? folderSelectionState(current, picked, excluded) : { state: "none", inside: 0 };
  const hasChildAlbums = (listing?.entries || []).length > 0;

  async function connectNas(share) {
    setMounting(true);
    setMountNote(share ? `Connecting ${share}…` : "Connecting the Synology NAS…");
    try {
      const result = await api.mountNas(share, { allShares: !share });
      const mounted = (result.items || []).filter((item) => item.mounted).map((item) => item.share);
      if (mounted.length) {
        setMountNote(
          result.host
            ? `Connected to ${result.host}: ${mounted.join(", ")}`
            : `Connected ${mounted.join(", ")}`,
        );
        setErr("");
      } else {
        setMountNote("");
        setErr(result.error || "Could not mount the NAS. Sign in if Finder asks, then try again.");
      }
    } catch (ex) {
      setMountNote("");
      setErr(ex.message);
    } finally {
      setMounting(false);
      await go("volumes", { quiet: true });
    }
  }

  useEffect(() => {
    let cancel = false;
    (async () => {
      const start = String(startPath || "").trim();
      const first = start && isAlbumPath(start) ? start : "volumes";
      await go(first);
      if (cancel) return;
      setMounting(true);
      setMountNote("Connecting the Synology NAS…");
      try {
        const result = await api.mountNas();
        if (cancel) return;
        const mounted = (result.items || []).filter((item) => item.mounted).map((item) => item.share);
        if (mounted.length) {
          setMountNote(result.host ? `Connected to ${result.host}` : "NAS connected");
          setErr("");
          await go(pathRef.current || first, { quiet: true });
        } else if (result.error) {
          setMountNote("");
        }
      } catch {
        if (!cancel) setMountNote("");
      } finally {
        if (!cancel) setMounting(false);
      }
    })();
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    if (!isVolumesPath(current)) return undefined;
    const id = setInterval(() => {
      go("volumes", { quiet: true });
    }, 4000);
    return () => clearInterval(id);
  }, [current]);

  const entries = listing?.entries || [];

  useEffect(() => {
    listRef.current?.focus();
  }, [listing?.path]);

  useEffect(() => {
    const row = listRef.current?.querySelector(`[data-folder-index="${active}"]`);
    row?.scrollIntoView({ block: "nearest" });
  }, [active, listing?.path]);

  function openEntry(entry) {
    if (!entry?.path) return;
    if (entry.mounted === false && !entry.from_catalog && /not mounted/i.test(entry.error || "")) {
      connectNas(entry.name);
      return;
    }
    go(entry.path);
  }

  function onListKey(event) {
    const typing = event.target.matches?.("input, textarea, [contenteditable]");
    if (typing) return;
    if (event.key === "Escape") {
      event.preventDefault();
      finish();
      return;
    }
    if (!entries.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(entries.length - 1, i + 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(0, i - 1));
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      openEntry(entries[active]);
      return;
    }
    if (event.key === " ") {
      event.preventDefault();
      const entry = entries[active];
      if (!entry || !isAlbumPath(entry.path)) return;
      tickPath(entry.path);
    }
  }

  return (
    <div
      className="modal-backdrop"
      onClick={finish}
      role="presentation"
    >
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={onListKey}
        role="dialog"
        aria-label="Choose folder"
      >
        <div className="page-head">
          <div>
            <h2>Choose photo folders</h2>
            <p className="hint">
              Tick a folder to include every album inside it. Untick an album to skip it.
              The app connects the Synology shares itself. It only reads files — it never writes
              back to those folders.
            </p>
          </div>
          <button
            className="ghost"
            type="button"
            onClick={finish}
            {...tip("Close. New albums start Find Known Faces on their own.")}
          >
            Close
          </button>
        </div>

        <div className="row" style={{ marginBottom: 10 }}>
          <button
            className="ghost"
            type="button"
            onClick={() => go("volumes")}
            {...tip("List NAS shares mounted in Finder.")}
          >
            NAS drives
          </button>
          <button
            className="ghost"
            type="button"
            onClick={() => go(null)}
            {...tip("Show folders on this Mac.")}
          >
            This computer
          </button>
          {listing?.parent ? (
            <button
              className="ghost"
              type="button"
              onClick={() => go(listing.parent)}
              {...tip("Open the folder above this one.")}
            >
              Go up
            </button>
          ) : null}
          <button
            className="ghost"
            type="button"
            disabled={mounting}
            onClick={() => connectNas()}
            {...tip("Connect the Synology NAS using the login already saved on this Mac.")}
          >
            {mounting ? "Connecting…" : "Connect NAS"}
          </button>
          <button
            className="ghost"
            type="button"
            onClick={() => go(current || "volumes", { quiet: true })}
            {...tip("Reload this list.")}
          >
            Refresh
          </button>
        </div>

        <div className="breadcrumb">
          {current
            ? crumbParts(current).map((crumb) => {
                const crumbState = folderSelectionState(crumb.path, picked, excluded).state;
                return (
                  <button
                    key={crumb.path}
                    type="button"
                    className={`crumb${crumbState === "all" ? " picked" : crumbState === "partial" ? " partial" : ""}`}
                    onClick={() => go(crumb.path)}
                  >
                    {crumb.label}
                  </button>
                );
              })
            : "Computer"}
        </div>
        {listing?.error && !listing?.from_catalog ? <p className="error">{listing.error}</p> : null}
        {listing?.from_catalog ? (
          <p className="hint">
            This share is not mounted. Albums already in the catalog are listed so you can still
            open and tick them. Connect NAS when you want to scan new photos.
          </p>
        ) : null}
        {err ? <p className="error">{err}</p> : null}
        {loading || mounting ? (
          <p className="hint">{mountNote || "Reading folder… NAS shares can take a moment."}</p>
        ) : mountNote && isVolumesPath(current) ? (
          <p className="hint">{mountNote}</p>
        ) : null}
        {!loading && !mounting && isVolumesPath(current) && (listing?.entries || []).length > 0
        && (listing.entries || []).every((entry) => entry.mounted === false) ? (
          <p className="hint">
            The Synology share is on the network but not connected. Click Connect NAS — Finder
            may ask for the login once, then this Mac remembers it.
          </p>
        ) : null}

        {picked.length ? (
          <div className="picked-folders" aria-label="Selected folders">
            {picked.map((path) => (
              <button
                key={path}
                type="button"
                className="picked-chip"
                onClick={() => {
                  const next = picked.filter((item) => item !== path);
                  setPicked(next);
                  onSelect(next, { scan: false });
                }}
                {...tip(`Remove ${folderLabel(path)} from the selection.`)}
              >
                {folderLabel(path)}
                <span aria-hidden="true"> ×</span>
              </button>
            ))}
          </div>
        ) : null}

        {current && !isVolumesPath(current) ? (
          <div className={`folder-current ${currentSel.state === "all" ? "picked" : currentSel.state === "partial" ? "partial" : ""}`}>
            <FolderCheck
              checked={currentSel.state === "all"}
              partial={currentSel.state === "partial"}
              name={folderLabel(current)}
              tipProps={tip(
                currentSel.state === "all"
                  ? hasChildAlbums
                    ? `Stop scanning ${folderLabel(current)} and every album inside it.`
                    : `Remove ${folderLabel(current)} from the list.`
                  : currentSel.state === "partial"
                    ? `Include every album in ${folderLabel(current)} again.`
                    : hasChildAlbums
                      ? `Include ${folderLabel(current)} and every album inside it.`
                      : `Add ${folderLabel(current)} to the list.`,
              )}
              onChange={() => tickPath(current)}
            />
            <div className="folder-current-copy">
              <strong>
                {hasChildAlbums ? `All albums in ${folderLabel(current)}` : folderLabel(current)}
              </strong>
              <span className="hint">
                {hasChildAlbums
                  ? currentSel.state === "all"
                    ? "Selected. Untick an album below to skip it."
                    : currentSel.state === "partial"
                      ? `${currentSel.inside === 1 ? "1 album" : `${currentSel.inside} albums`} skipped. Untick more below, or tick this to include all.`
                      : "Tick to include this folder and every album inside it."
                  : currentSel.state === "all"
                    ? "This album is selected"
                    : "Add this album to the list"}
              </span>
            </div>
          </div>
        ) : null}

        <div className="folder-list" ref={listRef} role="listbox" aria-label="Folders" tabIndex={0} onKeyDown={onListKey}>
          {entries.map((entry, index) => {
            const { state, inside } = folderSelectionState(entry.path, picked, excluded);
            const checked = state === "all";
            const partial = state === "partial";
            const skipped = isSkipped(entry.path, excluded);
            const catalog = Boolean(entry.from_catalog || listing?.from_catalog);
            const unmounted = entry.mounted === false && !catalog;
            const disabled = unmounted && !isAlbumPath(entry.path);
            const hintBits = [entry.hint || "Folder"];
            if (catalog) hintBits[0] = "In catalog";
            if (unmounted) hintBits.push("Not mounted");
            if (skipped) hintBits.push("Skipped");
            else if (partial) hintBits.push(inside === 1 ? "1 album skipped" : `${inside} albums skipped`);
            if (entry.error && !catalog && !hintBits.some((bit) => entry.error.startsWith(bit))) hintBits.push(entry.error);
            return (
              <div
                key={entry.path}
                data-folder-index={index}
                className={`folder-row ${checked ? "picked" : partial ? "partial" : ""}${unmounted || catalog ? " offline" : ""}${index === active ? " active" : ""}`}
                role="option"
                aria-selected={index === active}
              >
                <FolderCheck
                  checked={checked}
                  partial={partial}
                  disabled={disabled}
                  name={entry.name}
                  tipProps={tip(
                    partial
                      ? `${entry.name} has ${inside === 1 ? "an album" : `${inside} albums`} skipped. Tick it to include every album inside, or open it to skip more.`
                      : checked
                        ? `Skip ${entry.name}. Other albums stay included.`
                        : `Include ${entry.name}${skipped ? " again" : ""}.`,
                  )}
                  onChange={() => {
                    if (disabled) return;
                    tickPath(entry.path);
                  }}
                />
                <button
                  type="button"
                  className="folder-open"
                  onClick={() => openEntry(entry)}
                  {...tip(`Open ${entry.name}`)}
                >
                  <span className="folder-name">{entry.name}</span>
                  <span className="hint">{hintBits.join(" · ")}</span>
                </button>
              </div>
            );
          })}
          {!loading && listing && (listing.entries || []).length === 0 ? (
            <p className="hint">
              {isVolumesPath(current)
                ? "No NAS share is connected. Click Connect NAS."
                : (listing.image_count || 0) > 0
                  ? `This album has ${listing.image_count} ${listing.image_count === 1 ? "photo" : "photos"} and no subfolders.`
                  : "No subfolders and no photos in this folder."}
            </p>
          ) : null}
        </div>

        <div className="picker-foot">
          <span className="hint">
            {current
              ? `${listing?.image_count ?? 0} ${listing?.image_count === 1 ? "photo" : "photos"} here (not counting subfolders)${
                  currentSel.state === "all"
                    ? hasChildAlbums
                      ? " · all albums included"
                      : " · this album is selected"
                    : currentSel.state === "partial"
                      ? ` · ${currentSel.inside === 1 ? "1 album" : `${currentSel.inside} albums`} skipped`
                      : ""
                }`
              : "Open a NAS volume, then tick a folder to include every album inside it."}
          </span>
          <button
            type="button"
            onClick={finish}
            {...tip("Keep these albums. New ones start Find Known Faces automatically.")}
          >
            {picked.length === 0
              ? "Done"
              : picked.length === 1
                ? "Done · 1 album"
                : `Done · ${picked.length} albums`}
          </button>
        </div>
      </div>
    </div>
  );
}
