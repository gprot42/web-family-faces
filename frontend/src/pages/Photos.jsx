import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { addedFolderPaths, folderIsIndexed, folderLabel, photoInFolders, readImportFolders, writeImportFolders } from "../folders.js";
import { PHOTO_CHANGE_EVENT } from "../photoMenu.js";
import JobGauge from "../components/JobGauge.jsx";
import { beginPlay } from "../play.js";
import { tip } from "../tip.js";
import ViewSwitch from "../components/ViewSwitch.jsx";
import LabeledPhoto from "../components/LabeledPhoto.jsx";
import NamesToggle from "../components/NamesToggle.jsx";
import FolderPicker from "../components/FolderPicker.jsx";
import { photoTagHref, tagHref } from "../components/PhotoTags.jsx";

function folderOf(path) {
  const parts = (path || "").split("/").filter(Boolean);
  return parts.length >= 2 ? parts[parts.length - 2] : "Photos";
}

function photosInExactFolder(folderPath, items) {
  const prefix = String(folderPath || "").replace(/\/$/, "");
  if (!prefix) return items || [];
  return (items || []).filter((p) => {
    const parent = String(p.path || "").replace(/\/[^/]+$/, "");
    return parent === prefix;
  });
}

function folderAnchor(name) {
  return `folder-${encodeURIComponent(name).replace(/%/g, "")}`;
}

const FOLDER_SEL_KEY = "photosort-folder-sel";

function readFolderSel() {
  try {
    const raw = JSON.parse(localStorage.getItem(FOLDER_SEL_KEY) || "null");
    if (!raw || typeof raw !== "object") return null;
    return raw;
  } catch {
    return null;
  }
}

function writeFolderSel(sel) {
  try {
    if (!sel) localStorage.removeItem(FOLDER_SEL_KEY);
    else localStorage.setItem(FOLDER_SEL_KEY, JSON.stringify(sel));
  } catch {
    /* private mode */
  }
}

function writeFolderHash(hash) {
  const next = `${window.location.pathname}${window.location.search}${hash || ""}`;
  const cur = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  if (cur !== next) window.history.replaceState(null, "", next);
}

function catalogTree(catalog) {
  const ungrouped = [];
  const grouped = new Map();
  function ensureGroup(path, name) {
    if (!grouped.has(path)) {
      grouped.set(path, { name: name || folderLabel(path), path, albums: [], total: 0 });
    }
    return grouped.get(path);
  }
  for (const album of catalog || []) {
    if (!album?.path || !(album.photos > 0)) continue;
    const groupPath = album.group_path || "";
    if (groupPath && groupPath === album.path) {
      // Root files in a container folder. Count them on the group, do not list
      // the container as a nested album (that fetch would dump every grandchild).
      ensureGroup(album.path, album.group || album.folder).total += album.photos || 0;
      continue;
    }
    if (!groupPath) {
      ungrouped.push(album);
      continue;
    }
    const pack = ensureGroup(groupPath, album.group || folderLabel(groupPath));
    pack.albums.push(album);
    pack.total += album.photos || 0;
  }
  const groupPaths = new Set(grouped.keys());
  const leaves = ungrouped.filter((album) => !groupPaths.has(album.path));
  const groups = [...grouped.values()].filter((item) => item.total > 0);
  groups.sort((a, b) => a.name.localeCompare(b.name));
  for (const group of groups) {
    group.albums.sort((a, b) => (b.photos || 0) - (a.photos || 0) || a.folder.localeCompare(b.folder));
  }
  leaves.sort((a, b) => a.folder.localeCompare(b.folder));
  return { ungrouped: leaves, groups };
}

function fileStem(name) {
  return (name || "").replace(/\.[^.]+$/, "").toLowerCase();
}

function preferLargerCopy(photos) {
  const best = new Map();
  for (const p of photos) {
    const key = `${folderOf(p.path)}/${fileStem(p.filename)}`;
    const area = (p.width || 0) * (p.height || 0);
    const prev = best.get(key);
    if (!prev || area > (prev.width || 0) * (prev.height || 0)) best.set(key, p);
  }
  return [...best.values()];
}

const ALBUM_PAGE = 36;

async function loadAllPhotos(params = {}, onPage) {
  const page = 500;
  let items = [];
  let total = 0;
  let offset = 0;
  while (true) {
    const batch = await api.photos({ ...params, offset, limit: page });
    total = batch.total || 0;
    items = items.concat(batch.items || []);
    if (onPage) onPage({ items, total });
    offset += page;
    if (items.length >= total || !(batch.items || []).length) break;
  }
  return { items, total };
}

function photoLink(photoId, personId) {
  return personId ? `/photos/${photoId}?person=${personId}` : `/photos/${photoId}`;
}

export default function Photos() {
  const [params] = useSearchParams();
  const byPerson = params.get("by") === "person";
  const byTag = params.get("by") === "tag";
  const personFilter = params.get("person") || "";
  const tagFilter = params.get("tag") || "";
  if (byPerson) return <PhotoByPerson personFilter={personFilter} />;
  if (byTag) return <PhotoByTag tagFilter={tagFilter} />;
  return <FolderPhotos />;
}

function FolderPhotos() {
  const nav = useNavigate();
  const [data, setData] = useState({ items: [], total: 0 });
  const [albumPhotos, setAlbumPhotos] = useState({});
  const [q, setQ] = useState("");
  const [unidentified, setUnidentified] = useState(false);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [job, setJob] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [resetNote, setResetNote] = useState("");
  const [saved, setSaved] = useState(() => readImportFolders());
  const [picker, setPicker] = useState(false);
  const [scanErr, setScanErr] = useState("");
  const [catalog, setCatalog] = useState(null);
  const savedAtPickerOpen = useRef(saved);
  const albumPhotosRef = useRef({});
  const loadingAlbums = useRef(false);
  const [openPaths, setOpenPaths] = useState([]);
  const [activeFolder, setActiveFolder] = useState("");
  const [activePath, setActivePath] = useState("");
  const [folderQuery, setFolderQuery] = useState("");

  useEffect(() => {
    const sel = readFolderSel();
    if (!sel?.path) return undefined;
    setActiveFolder(sel.folder || "");
    setActivePath(sel.group || sel.path);
    setOpenPaths([sel.path]);
    fetchAlbum(sel.path).catch(() => {});
    return undefined;
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const jobs = await api.jobs();
        if (cancelled) return;
        const active = jobs?.active;
        const latest = jobs?.recent?.[0];
        const paused = !active && latest?.status === "paused" ? latest : null;
        const busy =
          active &&
          (active.type === "import" ||
            active.type === "pipeline" ||
            active.type === "scan" ||
            active.type === "identify");
        setImporting(!!busy);
        setJob(busy ? active : paused);
      } catch {
        /* backend may be starting */
      }
    }
    tick();
    const id = setInterval(tick, 8000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function fetchAlbum(path, limit = ALBUM_PAGE) {
    const batch = await api.photos({ q, unidentified, folder: [path], offset: 0, limit });
    const next = { items: preferLargerCopy(batch.items || []), total: batch.total || 0, path };
    setAlbumPhotos((cur) => {
      const merged = { ...cur, [path]: next };
      albumPhotosRef.current = merged;
      return merged;
    });
    return next;
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!saved.length) {
        setLoading(true);
        try {
          const stats = await api.stats();
          const root = stats?.folder;
          if (cancelled) return;
          if (root) {
            writeImportFolders([root]);
            setSaved([root]);
            return;
          }
        } catch {
          /* catalog root unavailable */
        }
        if (cancelled) return;
        setCatalog([]);
        setAlbumPhotos({});
        albumPhotosRef.current = {};
        setData({ items: [], total: 0 });
        setLoading(false);
        return;
      }
      loadingAlbums.current = true;
      setLoading(true);
      try {
        const listed = await api.nameFolders(saved);
        if (cancelled) return;
        const albums = listed.items || [];
        setCatalog(albums);
        const wanted = albums.filter((album) => album.path && album.photos > 0);
        if (q || unidentified) {
          const next = await loadAllPhotos({ q, unidentified, folder: saved }, (partial) => {
            if (!cancelled) {
              setData(partial);
              setLoading(false);
            }
          });
          if (!cancelled) setData(next);
        } else {
          setData({ items: [], total: wanted.reduce((n, album) => n + (album.photos || 0), 0) });
        }
      } catch {
        if (!cancelled) setCatalog([]);
      } finally {
        loadingAlbums.current = false;
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
      loadingAlbums.current = false;
    };
  }, [q, unidentified, saved]);

  const appliedSel = useRef("");

  function rememberSel(sel) {
    if (!sel?.hash) return;
    appliedSel.current = sel.hash;
    writeFolderSel(sel);
    writeFolderHash(`#${sel.hash}`);
  }

  useEffect(() => {
    if (!importing || !saved.length || q || unidentified) return undefined;
    const id = setInterval(async () => {
      if (loadingAlbums.current) return;
      try {
        const listed = await api.nameFolders(saved);
        const albums = listed.items || [];
        setCatalog(albums);
        for (const album of albums) {
          if (!album.path || !(album.photos > 0)) continue;
          const prev = albumPhotosRef.current[album.path];
          if (!prev || prev.total !== album.photos) fetchAlbum(album.path);
        }
      } catch {
        /* ignore */
      }
    }, 4000);
    return () => clearInterval(id);
  }, [importing, saved, q, unidentified]);

  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next?.id) return;
      setAlbumPhotos((cur) => {
        const merged = { ...cur };
        for (const [path, album] of Object.entries(merged)) {
          const items = next.hidden
            ? album.items.filter((p) => p.id !== next.id)
            : album.items.map((p) => (p.id === next.id ? { ...p, ...next } : p));
          merged[path] = { ...album, items };
        }
        albumPhotosRef.current = merged;
        return merged;
      });
      setData((cur) => {
        const items = cur.items || [];
        if (next.hidden) {
          const filtered = items.filter((p) => p.id !== next.id);
          return { ...cur, items: filtered, total: Math.max(0, (cur.total || filtered.length) - 1) };
        }
        return { ...cur, items: items.map((p) => (p.id === next.id ? { ...p, ...next } : p)) };
      });
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, []);

  const groups = useMemo(() => {
    if (q || unidentified) {
      const map = new Map();
      const items = saved.length
        ? data.items.filter((p) => photoInFolders(p.path, saved))
        : [];
      for (const p of preferLargerCopy(items)) {
        const key = folderOf(p.path);
        if (!map.has(key)) map.set(key, []);
        map.get(key).push(p);
      }
      return [...map.entries()].map(([name, photos]) => [name, photos, { path: photos[0]?.path, total: photos.length }]);
    }
    return (catalog || [])
      .filter((album) => album.path && album.photos > 0)
      .map((album) => {
        const loaded = albumPhotos[album.path] || { items: [], total: album.photos };
        return [album.folder, loaded.items || [], { path: album.path, total: loaded.total || album.photos }];
      });
  }, [data.items, saved, catalog, albumPhotos, q, unidentified]);

  const tree = useMemo(() => catalogTree(catalog), [catalog]);

  function openAlbums(albums, fetchFirst = 2) {
    const list = (albums || []).filter((album) => album?.path);
    if (!list.length) return;
    const show = list.slice(0, fetchFirst);
    setOpenPaths(show.map((album) => album.path));
    show.forEach((album) => {
      if (!(albumPhotosRef.current[album.path]?.items || []).length) fetchAlbum(album.path);
    });
  }

  function openGroup(group) {
    if (!group?.path) return;
    setActiveFolder(group.name);
    setActivePath(group.path);
    if (group.albums.length) {
      const show = group.albums.slice(0, 4);
      setOpenPaths([group.path, ...show.map((album) => album.path)]);
      if (!(albumPhotosRef.current[group.path]?.items || []).length) fetchAlbum(group.path);
      show.forEach((album) => {
        if (!(albumPhotosRef.current[album.path]?.items || []).length) fetchAlbum(album.path);
      });
    } else {
      setOpenPaths([group.path]);
      if (!(albumPhotosRef.current[group.path]?.items || []).length) fetchAlbum(group.path);
    }
    rememberSel({ hash: folderAnchor(group.name), path: group.path, folder: group.name, kind: "group" });
  }

  function openAlbum(album, parent) {
    if (!album?.path) return;
    const group = parent || tree.groups.find((item) => item.path === album.group_path);
    setActiveFolder(album.folder);
    setActivePath(group?.path || album.path);
    setOpenPaths([album.path]);
    if (!(albumPhotosRef.current[album.path]?.items || []).length) fetchAlbum(album.path);
    rememberSel({
      hash: folderAnchor(album.folder),
      path: album.path,
      folder: album.folder,
      group: group?.path || "",
      kind: "album",
    });
  }

  function selectFromLocation(treeNow) {
    if (q || unidentified) return;
    if (!treeNow.groups.length && !treeNow.ungrouped.length) return;
    const raw = window.location.hash.replace(/^#/, "");
    const savedSel = readFolderSel();
    const wanted = raw || savedSel?.hash || "";
    const albums = [...treeNow.ungrouped, ...treeNow.groups.flatMap((item) => item.albums)];
    if (wanted) {
      const group = treeNow.groups.find((item) => folderAnchor(item.name) === wanted);
      if (group) {
        if (appliedSel.current === wanted && activePath === group.path) return;
        openGroup(group);
        return;
      }
      const album = albums.find(
        (item) => folderAnchor(item.folder) === wanted || folderAnchor(`${item.group || ""}--${item.folder}`) === wanted,
      );
      if (album) {
        if (appliedSel.current === wanted && activeFolder === album.folder) return;
        openAlbum(album);
        return;
      }
      if (savedSel?.path) {
        const byPath = albums.find((item) => item.path === savedSel.path);
        if (byPath) {
          openAlbum(byPath);
          return;
        }
        const groupByPath = treeNow.groups.find((item) => item.path === savedSel.path);
        if (groupByPath) {
          openGroup(groupByPath);
          return;
        }
      }
    }
    if (appliedSel.current) return;
    if (treeNow.groups[0]) openGroup(treeNow.groups[0]);
    else if (treeNow.ungrouped[0]) openAlbum(treeNow.ungrouped[0]);
  }

  useEffect(() => {
    selectFromLocation(tree);
  }, [tree, q, unidentified]);

  useEffect(() => {
    function onHash() {
      appliedSel.current = "";
      selectFromLocation(tree);
    }
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    return () => {
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  }, [tree, q, unidentified]);

  useEffect(() => {
    if (!activeFolder) return undefined;
    const id = folderAnchor(activeFolder);
    const run = () => document.getElementById(id)?.scrollIntoView({ block: "start" });
    run();
    const t = window.setTimeout(run, 80);
    const t2 = window.setTimeout(run, 400);
    return () => {
      window.clearTimeout(t);
      window.clearTimeout(t2);
    };
  }, [activeFolder, openPaths]);

  const shown = (catalog || []).reduce((n, album) => n + (album.photos || 0), 0) || groups.reduce((n, [, photos]) => n + photos.length, 0);
  const libName = saved.length
    ? saved.length === 1
      ? folderLabel(saved[0])
      : `${folderLabel(saved[0])} + ${saved.length - 1} more`
    : "";
  const pending = useMemo(() => {
    if (catalog === null && !data.items.length) return [];
    if (catalog && catalog.length) {
      return catalog
        .filter((album) => album.path && !(album.photos > 0))
        .filter((album) => !data.items.some((p) => photoInFolders(p.path, [album.path])))
        .map((album) => album.path);
    }
    return saved.filter((path) => path && !folderIsIndexed(path, data.items, catalog));
  }, [saved, data.items, catalog]);

  async function scanFolders(paths) {
    const list = (paths || saved).filter(Boolean);
    if (!list.length) {
      setPicker(true);
      return;
    }
    setScanErr("");
    try {
      writeImportFolders(list);
      setSaved(list);
      await api.pipeline(list);
    } catch (ex) {
      setScanErr(ex.message);
    }
  }

  async function resetMatching(folder) {
    const all = !folder;
    const okGo = window.confirm(
      all
        ? "Clear auto-matched names in every folder? Names you typed stay. Photo files are not changed."
        : `Clear auto-matched names in “${folder}”? Names you typed stay. Photo files are not changed.`,
    );
    if (!okGo) return;
    setResetting(true);
    setResetNote("");
    try {
      const result = await api.resetMatching(folder || undefined);
      const albums = (catalog || []).filter((album) => album.path && album.photos > 0);
      const wanted = folder
        ? albums.filter((album) => album.folder === folder)
        : albums;
      await Promise.all(wanted.map((album) => fetchAlbum(album.path)));
      const n = result.faces_cleared || 0;
      setResetNote(
        all
          ? `Cleared ${n} auto-matched face${n === 1 ? "" : "s"}.`
          : `Cleared ${n} auto-matched face${n === 1 ? "" : "s"} in ${folder}.`,
      );
    } catch (ex) {
      setResetNote(ex.message || "Could not reset matching.");
    } finally {
      setResetting(false);
    }
  }

  const folderNeedle = folderQuery.trim().toLowerCase();
  const folderShown = (name) =>
    !folderNeedle || String(name || "").toLowerCase().includes(folderNeedle);
  const visibleGroups = tree.groups.filter((group) => folderShown(group.name));
  const visibleUngrouped = tree.ungrouped.filter((album) => folderShown(album.folder));

  return (
    <div className="folder-photos">
      <div className="page-head">
        <div>
          <p className="eyebrow">By album</p>
          <h1>Folder View</h1>
          <p className="lede">
            Full photos, grouped by the folder they live in.{" "}
            {loading
              ? "Loading photos…"
              : `${shown} photo${shown === 1 ? "" : "s"} in ${groups.length} folder${groups.length === 1 ? "" : "s"}${libName ? ` from ${libName}` : ""}.`}
            {shown !== data.total && !loading && (q || unidentified)
              ? ` ${data.total} match this filter.`
              : ""}{" "}
            {saved.length
              ? "Only selected albums appear here."
              : "No albums selected. Choose folders, then Find Known Faces."}{" "}
            Use <strong>View by person</strong> to see one person across every album.
          </p>
        </div>
        <div className="row">
          <NamesToggle />
          <ViewSwitch />
        </div>
      </div>
      {job ? (
        <JobGauge
          job={job}
          title={
            job.type === "identify"
              ? "Identifying faces"
              : job.type === "match"
                ? "Applying names"
                : job.type === "cluster"
                  ? "Grouping faces"
                  : "Finding known faces"
          }
        />
      ) : null}
      <div className="row" style={{ marginBottom: 16 }}>
        <input
          className="grow"
          type="search"
          placeholder="Filter by filename or folder"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
          }}
        />
        <label className="hint" {...tip("Hide photos where every face already has a name.")}>
          <input
            type="checkbox"
            checked={unidentified}
            onChange={(e) => {
              setUnidentified(e.target.checked);
            }}
          />{" "}
          Only photos with unnamed faces
        </label>
        <button
          type="button"
          className="secondary"
          onClick={() => {
            savedAtPickerOpen.current = saved;
            setPicker(true);
          }}
          {...tip("Choose another album. New albums start Find Known Faces when you are done.")}
        >
          Add folders
        </button>
        {importing || pending.length ? (
          <button
            type="button"
            className="secondary"
            disabled={importing || !pending.length}
            onClick={() => scanFolders(pending)}
            {...tip("Read albums that are not in the catalog yet. Already scanned albums stay as they are. Files are not moved.")}
          >
            {importing
              ? "Finding known faces…"
              : pending.length === 1
                ? "Find Known Faces in new folder"
                : "Find Known Faces in new folders"}
          </button>
        ) : null}
        <button
          type="button"
          className="secondary"
          disabled={!groups.length}
          onClick={() =>
            beginPlay(
              nav,
              groups.flatMap(([, photos]) => photos),
              { kind: "album", title: groups.length === 1 ? groups[0][0] : "Albums", from: "/photos" },
            )
          }
          {...tip("Play these albums in order, names on. Fullscreen. Space pauses.")}
        >
          Play albums
        </button>
        <button
          type="button"
          className="secondary"
          disabled={resetting || loading || !groups.length}
          onClick={() => resetMatching()}
          {...tip("Undo auto-matched names in every folder. Names you typed stay. Photos are not changed.")}
        >
          {resetting ? "Resetting…" : "Reset matching"}
        </button>
      </div>
      {scanErr ? <p className="error">{scanErr}</p> : null}
      {resetNote ? <p className="hint" style={{ marginTop: -8, marginBottom: 16 }}>{resetNote}</p> : null}
      {picker ? (
        <FolderPicker
          selected={saved}
          startPath={saved[0] || ""}
          onClose={() => setPicker(false)}
          onSelect={(paths, opts = {}) => {
            const next = Array.isArray(paths) ? paths.filter(Boolean) : [paths].filter(Boolean);
            writeImportFolders(next);
            setSaved(next);
            if (opts.scan) {
              const added = addedFolderPaths(next, savedAtPickerOpen.current);
              if (added.length && !importing) scanFolders(added);
            }
          }}
        />
      ) : null}
      {pending.length ? (
        <div className="person-chips" aria-label="Folders not scanned yet">
          {pending.map((path) => (
            <span key={path} className="person-chip">
              {folderLabel(path)}
              <span className="hint"> · not scanned yet</span>
            </span>
          ))}
        </div>
      ) : null}
      {pending.length ? (
        <section className="folder-block">
          <h2 className="folder-head">
            <span>
              {pending.length} album{pending.length === 1 ? "" : "s"}
              <span className="hint"> · not scanned yet</span>
            </span>
            <button
              type="button"
              className="ghost"
              disabled={importing}
              onClick={() => scanFolders(pending)}
              {...tip("Read these albums and show them here. Already scanned albums stay as they are.")}
            >
              {importing ? "Finding known faces…" : "Find Known Faces"}
            </button>
          </h2>
          <p className="hint">
            {importing
              ? "These albums are selected. Photos appear here as they are read. Files stay where they are."
              : "These albums are selected but not in the catalog yet. Find Known Faces to show the photos. Files stay where they are."}
          </p>
        </section>
      ) : null}
      {!loading && !groups.length && !pending.length ? (
        <p className="hint">
          {saved.length
            ? "Those albums are not in the catalog yet. Find Known Faces starts when you add them, or click the button above."
            : "No albums selected. Choose folders to see photos here."}
        </p>
      ) : null}
      {q || unidentified
        ? groups
            .filter(([, , meta]) => q || unidentified || !meta?.path || openPaths.includes(meta.path))
            .map(([folder, photos, meta]) => (
              <AlbumBlock
                key={meta?.path || folder}
                folder={folder}
                photos={photos}
                meta={meta}
                current={activeFolder === folder}
                onSelect={() => setActiveFolder(folder)}
                resetting={resetting}
                onPlay={async () => {
                  const path = meta?.path;
                  const all = path
                    ? preferLargerCopy((await loadAllPhotos({ folder: [path] })).items || [])
                    : photos;
                  beginPlay(nav, all, { kind: "album", title: folder, from: "/photos" });
                }}
                onReset={() => resetMatching(folder)}
                onNeed={() => {
                  if (meta?.path && !photos.length) fetchAlbum(meta.path);
                }}
                onMore={() => fetchAlbum(meta.path, Math.min((meta.total || photos.length) + ALBUM_PAGE, 500))}
              />
            ))
        : (
        <>
          {!tree.groups.length && !tree.ungrouped.length && openPaths[0] ? (
            <AlbumBlock
              folder={activeFolder || "Album"}
              photos={albumPhotos[openPaths[0]]?.items || []}
              meta={{
                path: openPaths[0],
                total: albumPhotos[openPaths[0]]?.total,
              }}
              current
              resetting={resetting}
              onPlay={async () => {
                const all = preferLargerCopy(
                  (await loadAllPhotos({ folder: [openPaths[0]] })).items || [],
                );
                beginPlay(nav, all, { kind: "album", title: activeFolder || "Album", from: "/photos" });
              }}
              onReset={() => resetMatching(activeFolder)}
              onNeed={() => fetchAlbum(openPaths[0])}
              onMore={() =>
                fetchAlbum(
                  openPaths[0],
                  Math.min((albumPhotos[openPaths[0]]?.total || 0) + ALBUM_PAGE, 500),
                )
              }
            />
          ) : null}
          {tree.groups.length || tree.ungrouped.length > 1 ? (
            <>
              <details className="folder-filter folder-filter-fold">
                <summary>
                  Folders
                  {activeFolder ? <span className="hint"> · {activeFolder}</span> : null}
                </summary>
                <input
                  type="search"
                  value={folderQuery}
                  onChange={(e) => setFolderQuery(e.target.value)}
                  placeholder="Find a folder"
                  aria-label="Find a folder"
                />
                <div className="person-chips" role="navigation" aria-label="Folders">
                {visibleGroups.map((group) => (
                  <a
                    key={group.path}
                    className={`person-chip${activePath === group.path ? " active" : ""}`}
                    href={`#${folderAnchor(group.name)}`}
                    aria-current={activePath === group.path ? "true" : undefined}
                    onClick={(event) => {
                      event.preventDefault();
                      openGroup(group);
                    }}
                    {...tip(`Show the ${group.albums.length} albums inside ${group.name}.`)}
                  >
                    {group.name}
                    <span className="hint">
                      {" "}
                      · {group.total} · {group.albums.length} albums
                    </span>
                  </a>
                ))}
                {visibleUngrouped.map((album) => (
                  <a
                    key={album.path}
                    className={`person-chip${activePath === album.path ? " active" : ""}`}
                    href={`#${folderAnchor(album.folder)}`}
                    aria-current={activePath === album.path ? "true" : undefined}
                    onClick={(event) => {
                      event.preventDefault();
                      openAlbum(album);
                    }}
                    {...tip(`Show photos in ${album.folder}.`)}
                  >
                    {album.folder}
                    <span className="hint"> · {album.photos}</span>
                  </a>
                ))}
                </div>
              </details>
              {tree.groups
                .filter((group) => activePath === group.path)
                .map((group) => (
                  <div
                    key={`${group.path}-nested`}
                    className="person-chips nested-albums"
                    role="navigation"
                    aria-label={`Albums in ${group.name}`}
                  >
                    {group.albums.map((album) => (
                      <a
                        key={album.path}
                        className={`person-chip${activeFolder === album.folder ? " active" : ""}`}
                        href={`#${folderAnchor(album.folder)}`}
                        onClick={(event) => {
                          event.preventDefault();
                          openAlbum(album, group);
                        }}
                        {...tip(`Show photos in ${album.folder}.`)}
                      >
                        {album.folder}
                        <span className="hint"> · {album.photos}</span>
                      </a>
                    ))}
                  </div>
                ))}
            </>
          ) : null}
          {tree.groups.map((group) => {
            const viewingGroup = activePath === group.path;
            const viewingWhole = viewingGroup && activeFolder === group.name;
            const kids = viewingWhole
              ? group.albums
              : group.albums.filter((album) => openPaths.includes(album.path));
            if (!viewingGroup && !kids.length) return null;
            const showMixed = viewingWhole && !group.albums.length;
            const loadedGroup = albumPhotos[group.path] || { items: [], total: group.total };
            const nestedCount = group.albums.reduce((n, album) => n + (album.photos || 0), 0);
            const rootTotal = Math.max(0, (group.total || 0) - nestedCount);
            const rootPhotos = photosInExactFolder(group.path, loadedGroup.items || []);
            return (
              <section
                key={group.path}
                id={folderAnchor(group.name)}
                className={`folder-block folder-group${activePath === group.path ? " current" : ""}`}
              >
                <h2 className="folder-head">
                  <span>
                    <span className="group-kind">Folder</span>
                    {group.name}
                    <span className="hint">
                      {" "}
                      · {group.total} photo{group.total === 1 ? "" : "s"} · {group.albums.length} album
                      {group.albums.length === 1 ? "" : "s"}
                    </span>
                  </span>
                </h2>
                {showMixed ? (
                  <AlbumBlock
                    folder={group.name}
                    photos={loadedGroup.items || []}
                    meta={{ path: group.path, total: loadedGroup.total || group.total }}
                    current
                    anchor={false}
                    onSelect={() => setActiveFolder(group.name)}
                    resetting={resetting}
                    onPlay={async () => {
                      const all = preferLargerCopy((await loadAllPhotos({ folder: [group.path] })).items || []);
                      beginPlay(nav, all, { kind: "album", title: group.name, from: "/photos" });
                    }}
                    onReset={() => resetMatching(group.name)}
                    onNeed={() => {
                      if (!(loadedGroup.items || []).length) fetchAlbum(group.path);
                    }}
                    onMore={() =>
                      fetchAlbum(group.path, Math.min((loadedGroup.total || group.total) + ALBUM_PAGE, 500))
                    }
                  />
                ) : null}
                {viewingWhole && !showMixed && rootTotal > 0 ? (
                  <AlbumBlock
                    folder={group.name}
                    photos={rootPhotos}
                    meta={{ path: group.path, total: rootTotal }}
                    current={activeFolder === group.name}
                    onSelect={() => setActiveFolder(group.name)}
                    resetting={resetting}
                    onPlay={async () => {
                      const all = photosInExactFolder(
                        group.path,
                        preferLargerCopy((await loadAllPhotos({ folder: [group.path] })).items || []),
                      );
                      beginPlay(nav, all, { kind: "album", title: group.name, from: "/photos" });
                    }}
                    onReset={() => resetMatching(group.name)}
                    onNeed={() => {
                      if (!(loadedGroup.items || []).length) fetchAlbum(group.path);
                    }}
                    onMore={() =>
                      fetchAlbum(group.path, Math.min((loadedGroup.items || []).length + ALBUM_PAGE, 500))
                    }
                  />
                ) : null}
                {kids.map((album) => {
                  const loaded = albumPhotos[album.path] || { items: [], total: album.photos };
                  return (
                    <AlbumBlock
                      key={album.path}
                      folder={album.folder}
                      photos={loaded.items || []}
                      meta={{ path: album.path, total: loaded.total || album.photos }}
                      current={activeFolder === album.folder}
                      onSelect={() => setActiveFolder(album.folder)}
                      resetting={resetting}
                      onPlay={async () => {
                        const all = preferLargerCopy((await loadAllPhotos({ folder: [album.path] })).items || []);
                        beginPlay(nav, all, { kind: "album", title: album.folder, from: "/photos" });
                      }}
                      onReset={() => resetMatching(album.folder)}
                      onNeed={() => {
                        if (!(loaded.items || []).length) fetchAlbum(album.path);
                      }}
                      onMore={() =>
                        fetchAlbum(album.path, Math.min((loaded.total || album.photos) + ALBUM_PAGE, 500))
                      }
                    />
                  );
                })}
              </section>
            );
          })}
          {tree.ungrouped
            .filter((album) => openPaths.includes(album.path))
            .map((album) => {
              const loaded = albumPhotos[album.path] || { items: [], total: album.photos };
              return (
                <AlbumBlock
                  key={album.path}
                  folder={album.folder}
                  photos={loaded.items || []}
                  meta={{ path: album.path, total: loaded.total || album.photos }}
                  current={activePath === album.path}
                  onSelect={() => {
                    setActiveFolder(album.folder);
                    setActivePath(album.path);
                  }}
                  resetting={resetting}
                  onPlay={async () => {
                    const all = preferLargerCopy((await loadAllPhotos({ folder: [album.path] })).items || []);
                    beginPlay(nav, all, { kind: "album", title: album.folder, from: "/photos" });
                  }}
                  onReset={() => resetMatching(album.folder)}
                  onNeed={() => {
                    if (!(loaded.items || []).length) fetchAlbum(album.path);
                  }}
                  onMore={() => fetchAlbum(album.path, Math.min((loaded.total || album.photos) + ALBUM_PAGE, 500))}
                />
              );
            })}
        </>
      )}
    </div>
  );
}

function AlbumBlock({ folder, photos, meta, current, onSelect, resetting, onPlay, onReset, onNeed, onMore, anchor = true }) {
  const ref = useRef(null);
  useEffect(() => {
    if (photos.length || !meta?.path) return undefined;
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      onNeed();
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          onNeed();
          io.disconnect();
        }
      },
      { rootMargin: "600px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [meta?.path, photos.length]);
  const total = meta?.total || photos.length;
  return (
    <section
      ref={ref}
      className={`folder-block album-block${current ? " current" : ""}`}
      id={anchor ? folderAnchor(folder) : undefined}
      onClick={() => onSelect?.()}
    >
      <header className="album-head">
        <div className="album-head-copy">
          <p className="eyebrow">Album</p>
          <h2>{folder}</h2>
          <p className="album-head-count">
            {total} photo{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="album-head-actions">
          <button
            type="button"
            className="secondary"
            onClick={onPlay}
            {...tip(`Play ${folder} in date order, names on. Fullscreen.`)}
          >
            Play
          </button>
          <button
            type="button"
            className="ghost"
            disabled={resetting}
            onClick={onReset}
            {...tip(`Undo auto-matched names in ${folder}. Names you typed stay.`)}
          >
            Reset matching
          </button>
        </div>
      </header>
      {photos.length ? (
        <div className="folder-gallery">
          {photos.map((p) => {
            const rot = (((Number(p.rotation) || 0) % 360) + 360) % 360;
            const swapped = rot === 90 || rot === 270;
            const tileAr =
              p.width > 0 && p.height > 0
                ? swapped
                  ? `${p.height} / ${p.width}`
                  : `${p.width} / ${p.height}`
                : "4 / 3";
            return (
            <div
              className="label-card"
              key={p.id}
              style={{ "--tile-ar": tileAr }}
            >
              <LabeledPhoto
                photo={p}
                src={p.thumb_url}
                to={photoLink(p.id)}
                toState={{ fullscreen: true, from: "/photos" }}
              />
              <div className="photo-caption">
                {p.taken_at ? p.taken_at.slice(0, 10) : p.filename}
                {p.comment ? <div className="photo-comment-snip">{p.comment}</div> : null}
              </div>
            </div>
            );
          })}
        </div>
      ) : (
        <p className="hint">Loading photos…</p>
      )}
      {meta?.path && total > photos.length && photos.length ? (
        <div className="album-more">
          <p>
            {photos.length} of {total}
          </p>
          <button type="button" className="secondary" onClick={onMore}>
            Show more photos
          </button>
        </div>
      ) : null}
    </section>
  );
}

const HIDE_UNKNOWN_KEY = "photosort-hide-unknown";

function readHideUnknown() {
  try {
    return localStorage.getItem(HIDE_UNKNOWN_KEY) === "1";
  } catch {
    return false;
  }
}

function PhotoByPerson({ personFilter }) {
  const nav = useNavigate();
  const [people, setPeople] = useState([]);
  const [sections, setSections] = useState([]);
  const [hideUnknown, setHideUnknown] = useState(readHideUnknown);

  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next?.id) return;
      setSections((cur) =>
        cur.map((section) => {
          const photos = next.hidden
            ? section.photos.filter((p) => p.id !== next.id)
            : section.photos.map((p) => (p.id === next.id ? { ...p, ...next } : p));
          return { ...section, photos, total: next.hidden ? Math.max(0, (section.total || photos.length) - 1) : section.total };
        }),
      );
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(HIDE_UNKNOWN_KEY, hideUnknown ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [hideUnknown]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      let listed;
      try {
        listed = await api.people(undefined, { lite: 1 });
      } catch {
        if (!cancelled) setSections([]);
        return;
      }
      const items = listed.items || [];
      const wanted = personFilter
        ? items.filter((p) => String(p.id) === String(personFilter))
        : [];
      if (!cancelled) {
        setPeople(items);
        setSections([]);
      }
      for (const person of wanted) {
        if (cancelled) return;
        try {
          const photos = await loadAllPhotos({ person_id: person.id });
          if (cancelled) return;
          setSections((cur) => [
            ...cur.filter((s) => String(s.person.id) !== String(person.id)),
            { person, photos: preferLargerCopy(photos.items || []), total: photos.total || 0 },
          ]);
        } catch {
          /* skip a person whose album failed */
        }
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [personFilter]);

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Photos</p>
          <h1>View by person</h1>
          <p className="lede">
            Full photos from the NAS albums, grouped by who is in them. This page shows saved
            previews. Open a picture to read the original file if the share is mounted. Folder View
            keeps the same pictures in album order.
          </p>
        </div>
        <div className="row">
          <label className="hint" {...tip("Hide Name unknown people, and unnamed faces on the photos. Named people stay.")}>
            <input
              type="checkbox"
              checked={hideUnknown}
              onChange={(e) => setHideUnknown(e.target.checked)}
            />{" "}
            Hide unknown
          </label>
          <NamesToggle />
          <ViewSwitch personId={personFilter || undefined} />
        </div>
      </div>
      {people.length > 1 ? (
        <div className="person-chips" role="tablist" aria-label="Named people">
          <Link
            className={`person-chip ${!personFilter ? "active" : ""}`}
            to="/photos?by=person"
            {...tip("Show every named person in a separate list.")}
          >
            Everyone
          </Link>
          {(hideUnknown ? people.filter((p) => !p.unknown_name) : people).map((p) => (
            <Link
              key={p.id}
              className={`person-chip ${String(personFilter) === String(p.id) ? "active" : ""}`}
              to={`/photos?by=person&person=${p.id}`}
              {...tip(`Show only photos of ${p.name}.`)}
            >
              {p.unknown_name ? "Name unknown" : p.name}
            </Link>
          ))}
        </div>
      ) : null}
      {sections.length === 0 ? (
        <p className="hint">
          {!people.length
            ? "Name someone on To name first. View by person then lists each person separately."
            : personFilter
              ? "Loading photos…"
              : "Pick a person to see their photos."}
        </p>
      ) : (
        sections.map(({ person, photos, total }) => (
          <section key={person.id} className="folder-block">
            <h2 className="folder-head">
              <span>
                <span className="group-kind">Person</span>
                <Link to={`/people/${person.id}`}>{person.name}</Link>
                <span className="hint"> · {total} photo{total === 1 ? "" : "s"}</span>
              </span>
              <button
                type="button"
                className="secondary"
                disabled={!photos.length}
                onClick={() =>
                  beginPlay(nav, photos, {
                    kind: "person",
                    title: person.unknown_name ? "Name unknown" : person.name,
                    personId: person.id,
                    from: personFilter
                      ? `/photos?by=person&person=${person.id}`
                      : "/photos?by=person",
                  })
                }
                {...tip(`Play photos of ${person.name} in date order, names on. Fullscreen.`)}
              >
                Play person
              </button>
            </h2>
            <div className="label-grid">
              {photos.map((p) => (
                <div className="label-card" key={p.id}>
                  <LabeledPhoto
                    photo={p}
                    src={p.thumb_url}
                    hideUnknown={hideUnknown}
                    to={photoLink(p.id, person.id)}
                    toState={{
                      fullscreen: true,
                      from: personFilter
                        ? `/photos?by=person&person=${person.id}`
                        : "/photos?by=person",
                    }}
                  />
                  <div className="meta">
                    {p.filename}
                    {p.comment ? <div className="photo-comment-snip">{p.comment}</div> : null}
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}

function PhotoByTag({ tagFilter }) {
  const nav = useNavigate();
  const [tags, setTags] = useState([]);
  const [photos, setPhotos] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next?.id) return;
      setPhotos((cur) => {
        if (next.hidden) return cur.filter((p) => p.id !== next.id);
        return cur.map((p) => (p.id === next.id ? { ...p, ...next } : p));
      });
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const listed = await api.photoTags();
        if (cancelled) return;
        setTags(listed.items || []);
        if (!tagFilter) {
          setPhotos([]);
          setTotal(0);
          return;
        }
        const batch = await loadAllPhotos({ tag: tagFilter });
        if (cancelled) return;
        setPhotos(preferLargerCopy(batch.items || []));
        setTotal(batch.total || 0);
      } catch {
        if (!cancelled) {
          setTags([]);
          setPhotos([]);
          setTotal(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [tagFilter]);

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Photos</p>
          <h1>View by tag</h1>
          <p className="lede">
            Full photos grouped by a tag you put on the picture. Add a tag on a photo, then pick
            it here. Originals are not changed.
          </p>
        </div>
        <div className="row">
          <NamesToggle />
          <ViewSwitch />
        </div>
      </div>
      {tags.length ? (
        <div className="person-chips" role="tablist" aria-label="Custom tags">
          {tags.map((item) => (
            <Link
              key={item.tag}
              className={`person-chip ${tagFilter === item.tag ? "active" : ""}`}
              to={tagHref(item.tag)}
              {...tip(`Show photos tagged ${item.tag}.`)}
            >
              {item.tag}
              <span className="hint"> · {item.photos}</span>
            </Link>
          ))}
        </div>
      ) : null}
      {!tags.length && !loading ? (
        <p className="hint">Add a tag on a photo. View by tag then lists each tag separately.</p>
      ) : null}
      {tagFilter && loading ? <p className="hint">Loading photos…</p> : null}
      {tagFilter && !loading ? (
        <section className="folder-block">
          <h2 className="folder-head">
            <span>
              <span className="group-kind">Tag</span>
              {tagFilter}
              <span className="hint">
                {" "}
                · {total} photo{total === 1 ? "" : "s"}
              </span>
            </span>
            <button
              type="button"
              className="secondary"
              disabled={!photos.length}
              onClick={() =>
                beginPlay(nav, photos, {
                  kind: "tag",
                  title: tagFilter,
                  tag: tagFilter,
                  from: tagHref(tagFilter),
                })
              }
              {...tip(`Play photos tagged ${tagFilter} in date order, names on. Fullscreen.`)}
            >
              Play tag
            </button>
          </h2>
          {photos.length ? (
            <div className="label-grid">
              {photos.map((p) => (
                <div className="label-card" key={p.id}>
                  <LabeledPhoto
                    photo={p}
                    src={p.thumb_url}
                    to={photoTagHref(p.id, tagFilter)}
                    toState={{ fullscreen: true, from: tagHref(tagFilter) }}
                  />
                  <div className="meta">
                    {p.filename}
                    {p.comment ? <div className="photo-comment-snip">{p.comment}</div> : null}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="hint">No photos have this tag yet.</p>
          )}
        </section>
      ) : null}
    </div>
  );
}
