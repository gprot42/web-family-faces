import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { ALL_FOLDERS_EVENT, addedFolderPaths, folderDisplayName, folderIsIndexed, folderLabel, FOLDER_TITLE_MAX, isFolderStarred, normalizeFolderPath, photoInFolders, readFolderTitles, readImportFolders, readStarredFolders, setFolderTitle, toggleStarredFolder, writeFolderTitles, writeImportFolders, writeStarredFolders } from "../folders.js";
import { PHOTO_CHANGE_EVENT } from "../photoMenu.js";
import JobGauge from "../components/JobGauge.jsx";
import { beginPlay } from "../play.js";
import { tip } from "../tip.js";
import ViewSwitch from "../components/ViewSwitch.jsx";
import LabeledPhoto from "../components/LabeledPhoto.jsx";
import NamesToggle, { usePhotoLabels } from "../components/NamesToggle.jsx";
import FolderPicker from "../components/FolderPicker.jsx";
import ConfirmAsk from "../components/ConfirmAsk.jsx";
import { photoTagHref, tagHref } from "../components/PhotoTags.jsx";
import { clearAlbumPos, readAlbumPos, writeAlbumPos } from "../albumPos.js";

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
const FOLDER_CATALOG_KEY = "photosort-folder-catalog-v1";

function loadCachedCatalog() {
  try {
    const raw = JSON.parse(localStorage.getItem(FOLDER_CATALOG_KEY) || "null");
    return Array.isArray(raw?.items) ? raw.items : [];
  } catch {
    return [];
  }
}

function saveCachedCatalog(items) {
  try {
    localStorage.setItem(FOLDER_CATALOG_KEY, JSON.stringify({ items: items || [] }));
  } catch {
    /* private mode or quota */
  }
}

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

function folderYear(name) {
  const s = String(name || "").trim();
  const head = s.match(/^(19|20)\d{2}(?=\s*[-.–]\s*|\.\s+)/);
  if (head) return head[0];
  const tail = s.match(/_(19|20)\d{2}$/);
  return tail ? tail[0].slice(1) : "";
}

function folderShortName(name, year) {
  if (!year) return String(name || "");
  const stripped = String(name || "")
    .replace(new RegExp(`^${year}(?:\\s*[-.–]\\s*|\\.\\s+)`), "")
    .replace(new RegExp(`_${year}$`), "")
    .trim();
  return stripped || String(name || "");
}

function folderIndex(tree, needle, starredPaths, titles) {
  const q = String(needle || "").trim().toLowerCase();
  const shown = (path, fallback) => folderDisplayName(path, fallback, titles);
  const match = (name, path) => {
    if (!q) return true;
    const hay = `${name || ""} ${shown(path, name)}`.toLowerCase();
    return hay.includes(q);
  };
  const starredList = (starredPaths || []).map(normalizeFolderPath).filter((path) => path && path !== "/");
  const starredSet = new Set(starredList);
  const order = new Map(starredList.map((path, i) => [path, i]));
  const entries = [];
  const seen = new Set();
  function addEntry(item) {
    if (!item?.key || seen.has(item.key)) return;
    seen.add(item.key);
    entries.push(item);
  }
  for (const group of tree.groups || []) {
    if (!match(group.name, group.path)) continue;
    addEntry({
      key: normalizeFolderPath(group.path),
      name: group.name,
      year: folderYear(group.name),
      photos: group.total,
      albums: group.albums.length,
      kind: "group",
      group,
    });
  }
  for (const album of tree.ungrouped || []) {
    if (!match(album.folder, album.path)) continue;
    addEntry({
      key: normalizeFolderPath(album.path),
      name: album.folder,
      year: folderYear(album.folder),
      photos: album.photos,
      albums: 0,
      kind: "album",
      album,
    });
  }
  const packed = entries.map((item) => {
    const year = item.year;
    const label = shown(item.key, item.name);
    return { ...item, title: folderShortName(label, year) };
  });
  const starred = packed.filter((item) => starredSet.has(item.key));
  for (const group of tree.groups || []) {
    for (const album of group.albums || []) {
      const key = normalizeFolderPath(album.path);
      if (!starredSet.has(key) || !match(album.folder, album.path) || starred.some((item) => item.key === key)) continue;
      const year = folderYear(album.folder);
      starred.push({
        key,
        name: album.folder,
        year,
        photos: album.photos,
        albums: 0,
        kind: "album",
        album,
        title: folderShortName(shown(key, album.folder), year),
      });
    }
  }
  starred.sort((a, b) => (order.get(a.key) ?? 0) - (order.get(b.key) ?? 0) || a.title.localeCompare(b.title));
  const buckets = new Map();
  for (const item of packed) {
    const year = item.year || "Other";
    if (!buckets.has(year)) buckets.set(year, []);
    buckets.get(year).push(item);
  }
  const years = [...buckets.keys()].sort((a, b) => {
    if (a === "Other") return 1;
    if (b === "Other") return -1;
    return Number(b) - Number(a);
  });
  for (const year of years) {
    buckets.get(year).sort((a, b) => a.title.localeCompare(b.title) || a.name.localeCompare(b.name));
  }
  const decades = [];
  const decadeSeen = new Set();
  for (const year of years) {
    const label = year === "Other" ? "Other" : `${Math.floor(Number(year) / 10) * 10}s`;
    if (decadeSeen.has(label)) continue;
    decadeSeen.add(label);
    decades.push({ label, year });
  }
  return {
    starred,
    years: years.map((year) => ({ year, items: buckets.get(year) })),
    decades,
  };
}

function FolderStarButton({ name, starred, onToggle }) {
  return (
    <button
      type="button"
      className="folder-star"
      aria-pressed={starred}
      aria-label={starred ? `Remove star from ${name}` : `Star ${name}`}
      onPointerDown={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onToggle();
      }}
      {...tip(starred ? "Stop listing this folder at the top." : "Keep this folder at the top of Folder View.")}
    >
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 3.1 14.7 9h6.6l-5.3 4 2 6.6L12 16.2 5.9 19.6l2-6.6-5.3-4h6.6L12 3.1z"
          fill={starred ? "currentColor" : "none"}
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

function countLabel(n, word) {
  const value = Number(n) || 0;
  return `${value.toLocaleString()} ${word}${value === 1 ? "" : "s"}`;
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

const ALBUM_PAGE = 50;

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
  const loc = useLocation();
  const [data, setData] = useState({ items: [], total: 0 });
  const [albumPhotos, setAlbumPhotos] = useState({});
  const [q, setQ] = useState("");
  const [unidentified, setUnidentified] = useState(false);
  const [importing, setImporting] = useState(false);
  const [job, setJob] = useState(null);
  const [resetting, setResetting] = useState(false);
  const [resetNote, setResetNote] = useState("");
  const [saved, setSaved] = useState(() => readImportFolders());
  const [picker, setPicker] = useState(false);
  const [scanConfirm, setScanConfirm] = useState(null);
  const [resetAsk, setResetAsk] = useState(null);
  const [scanErr, setScanErr] = useState("");
  const [catalog, setCatalog] = useState(() => {
    const cached = loadCachedCatalog();
    return cached.length ? cached : null;
  });
  const [loading, setLoading] = useState(() => !loadCachedCatalog().length);
  const savedAtPickerOpen = useRef(saved);
  const albumPhotosRef = useRef({});
  const loadingAlbums = useRef(false);
  const [openPaths, setOpenPaths] = useState([]);
  const openPathsRef = useRef([]);
  openPathsRef.current = openPaths;
  const [activeFolder, setActiveFolder] = useState("");
  const [activePath, setActivePath] = useState("");
  const [folderQuery, setFolderQuery] = useState("");
  const [starredFolders, setStarredFolders] = useState(readStarredFolders);
  const [folderTitles, setFolderTitles] = useState(readFolderTitles);
  const [folderMenu, setFolderMenu] = useState(null);

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
      const prev = cur[path];
      if ((prev?.items?.length || 0) >= (next.items?.length || 0) && prev?.path === path) {
        return cur;
      }
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
      if (!(catalog || []).length) setLoading(true);
      try {
        const indexed = await api.nameFolders();
        if (cancelled) return;
        const indexedItems = indexed.items || [];
        if (indexedItems.length) {
          setCatalog(indexedItems);
          saveCachedCatalog(indexedItems);
          setLoading(false);
          setData({
            items: [],
            total: indexedItems.reduce((n, album) => n + (album.photos || 0), 0),
          });
        }
      } catch {
        /* keep cached albums while the full catalog loads */
      }
      if (q || unidentified) {
        try {
          const listed = await api.nameFolders(saved);
          if (cancelled) return;
          const albums = listed.items || [];
          setCatalog(albums);
          saveCachedCatalog(albums);
          const next = await loadAllPhotos({ q, unidentified, folder: saved }, (partial) => {
            if (!cancelled) {
              setData(partial);
              setLoading(false);
            }
          });
          if (!cancelled) setData(next);
        } catch {
          if (!cancelled) setCatalog((cur) => cur || []);
        } finally {
          loadingAlbums.current = false;
          if (!cancelled) setLoading(false);
        }
        return;
      }
      loadingAlbums.current = false;
      if (!cancelled) setLoading(false);
      api
        .nameFolders(saved, { disk: true })
        .then((listed) => {
          if (cancelled) return;
          const albums = listed.items || [];
          if (!albums.length) return;
          setCatalog(albums);
          saveCachedCatalog(albums);
          setData({
            items: [],
            total: albums.filter((album) => album.path && album.photos > 0).reduce((n, album) => n + (album.photos || 0), 0),
          });
        })
        .catch(() => {});
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
        saveCachedCatalog(albums);
        for (const path of openPathsRef.current) {
          if (!path) continue;
          const prev = albumPhotosRef.current[path];
          if (!prev) fetchAlbum(path);
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

  function showAllFolders() {
    appliedSel.current = "";
    setActiveFolder("");
    setActivePath("");
    setOpenPaths([]);
    setFolderQuery("");
    writeFolderSel(null);
    writeFolderHash("");
    const path = window.location.pathname;
    const search = window.location.search;
    const hash = window.location.hash;
    if (path !== "/photos" || search || hash) nav("/photos", { replace: true });
  }

  function selectFromLocation(treeNow) {
    if (q || unidentified) return;
    if (!treeNow.groups.length && !treeNow.ungrouped.length) return;
    const wanted = (loc.hash || window.location.hash).replace(/^#/, "");
    const albums = [...treeNow.ungrouped, ...treeNow.groups.flatMap((item) => item.albums)];
    if (!wanted) {
      if (appliedSel.current || activeFolder || activePath || openPaths.length) {
        appliedSel.current = "";
        setActiveFolder("");
        setActivePath("");
        setOpenPaths([]);
      }
      return;
    }
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
    showAllFolders();
  }

  useEffect(() => {
    selectFromLocation(tree);
  }, [tree, q, unidentified, loc.hash, loc.pathname]);

  useEffect(() => {
    function onAllFolders() {
      showAllFolders();
    }
    window.addEventListener(ALL_FOLDERS_EVENT, onAllFolders);
    return () => window.removeEventListener(ALL_FOLDERS_EVENT, onAllFolders);
  }, []);

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

  const restoredFor = useRef("");

  useEffect(() => {
    if (!activeFolder) {
      restoredFor.current = "";
      return undefined;
    }
    const hash = folderAnchor(activeFolder);
    const pos = readAlbumPos();
    const restore = pos?.hash === hash ? pos : null;
    const restoreId = restore?.photoId ? Number(restore.photoId) : 0;

    if (restore?.path && restoreId) {
      const have = albumPhotosRef.current[restore.path]?.items || [];
      const found = have.some((p) => Number(p.id) === restoreId);
      const want = Math.min(500, Math.max(ALBUM_PAGE, Number(restore.count) || 0));
      if (!found && want > have.length) fetchAlbum(restore.path, want);
    }

    if (restoreId) {
      const tile = document.getElementById(`photo-tile-${restoreId}`);
      if (tile) {
        tile.scrollIntoView({ block: "center", inline: "nearest" });
        restoredFor.current = hash;
        clearAlbumPos();
        return undefined;
      }
      document.getElementById(hash)?.scrollIntoView({ block: "start" });
      return undefined;
    }

    if (restoredFor.current === hash) return undefined;
    const run = () => document.getElementById(hash)?.scrollIntoView({ block: "start" });
    run();
    const t = window.setTimeout(run, 80);
    const t2 = window.setTimeout(run, 400);
    return () => {
      window.clearTimeout(t);
      window.clearTimeout(t2);
    };
  }, [activeFolder, openPaths, albumPhotos]);

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

  function requestScan(paths) {
    const list = (paths || pending).filter(Boolean);
    if (!list.length) {
      setPicker(true);
      return;
    }
    setScanConfirm(list);
  }

  function askResetMatching(folder) {
    setResetAsk(folder || true);
  }

  async function resetMatching(folder) {
    const all = !folder;
    setResetAsk(null);
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

  const index = useMemo(
    () => folderIndex(tree, folderQuery, starredFolders, folderTitles),
    [tree, folderQuery, starredFolders, folderTitles],
  );

  function albumLabel(path, fallback) {
    return folderDisplayName(path, fallback, folderTitles);
  }

  function openFolderMenu(event, path, name) {
    if (!path) return;
    event.preventDefault();
    event.stopPropagation();
    setFolderMenu({ x: event.clientX, y: event.clientY, path, name });
  }

  function saveFolderName(path, original, draft) {
    const cleaned = String(draft || "").trim();
    const next = setFolderTitle(path, cleaned === String(original || "").trim() ? "" : cleaned, folderTitles);
    writeFolderTitles(next);
    setFolderTitles(next);
  }

  function toggleStar(path) {
    setStarredFolders((cur) => {
      const next = toggleStarredFolder(path, cur);
      writeStarredFolders(next);
      return next;
    });
  }

  function folderTile(item, { showYear = false } = {}) {
    const active =
      item.kind === "group"
        ? activePath === item.group.path
        : activePath === item.album.path || openPaths.includes(item.album.path);
    const starred = isFolderStarred(item.key, starredFolders);
    const year = showYear ? item.year : "";
    const meta = `${countLabel(item.photos, "photo")}${item.albums > 1 ? ` · ${countLabel(item.albums, "album")}` : ""}`;
    const label = year ? `${year}. ${item.title}. ${meta}` : `${item.title}. ${meta}`;
    return (
      <a
        key={item.key}
        className={`folder-tile${active ? " active" : ""}${starred ? " starred" : ""}`}
        href={`#${folderAnchor(item.name)}`}
        aria-label={label}
        onContextMenu={(event) => openFolderMenu(event, item.key, item.name)}
        onClick={(event) => {
          event.preventDefault();
          if (item.kind === "group") openGroup(item.group);
          else openAlbum(item.album);
        }}
        {...tip(
          item.kind === "group"
            ? `Show the ${item.albums} albums inside ${item.name}.`
            : `Show photos in ${item.name}.`,
        )}
      >
        <span className="folder-tile-top">
          <span className="folder-tile-name">
            {year ? <span className="folder-tile-year">{year}</span> : null}
            {item.title}
          </span>
          <FolderStarButton name={item.name} starred={starred} onToggle={() => toggleStar(item.key)} />
        </span>
        <span className="folder-tile-meta">{meta}</span>
      </a>
    );
  }

  return (
    <div className="folder-photos">
      <div className="page-head">
        <div>
          <p className="eyebrow">By album</p>
          <h1>Folder View</h1>
          <p className="lede">
            Full photos, grouped by the folder they live in.{" "}
            {loading && !(catalog || []).length
              ? "Loading folders…"
              : activeFolder
                ? `${shown} photo${shown === 1 ? "" : "s"} in ${groups.length} folder${groups.length === 1 ? "" : "s"}${libName ? ` from ${libName}` : ""}.`
                : `Choose a folder to see photos.${libName ? ` ${libName}.` : ""}`}
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
            onClick={() => requestScan(pending)}
            {...tip("Show the albums that are not in the catalog yet, then confirm. Already scanned albums stay as they are. Files are not moved.")}
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
          onClick={() => askResetMatching()}
          {...tip("Undo auto-matched names in every folder. Names you typed stay. Photos are not changed.")}
        >
          {resetting ? "Resetting…" : "Reset matching"}
        </button>
      </div>
      {scanErr ? <p className="error">{scanErr}</p> : null}
      {resetNote ? <p className="hint" style={{ marginTop: -8, marginBottom: 16 }}>{resetNote}</p> : null}
      {scanConfirm ? (
        <ScanConfirm
          paths={scanConfirm}
          titles={folderTitles}
          onCancel={() => setScanConfirm(null)}
          onConfirm={() => {
            const list = scanConfirm;
            setScanConfirm(null);
            scanFolders(list);
          }}
        />
      ) : null}
      {resetAsk ? (
        <ConfirmAsk
          title={resetAsk === true ? "Reset matching in every folder?" : `Reset matching in “${resetAsk}”?`}
          body="Clear auto-matched names. Names you typed stay. Photo files are not changed."
          onCancel={() => setResetAsk(null)}
          onConfirm={() => resetMatching(resetAsk === true ? undefined : resetAsk)}
        />
      ) : null}
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
              if (added.length && !importing) requestScan(added);
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
              onClick={() => requestScan(pending)}
              {...tip("Show these albums, then confirm. Already scanned albums stay as they are.")}
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
                label={meta?.path ? albumLabel(meta.path, folder) : folder}
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
                onReset={() => askResetMatching(folder)}
                onRename={
                  meta?.path
                    ? (event) => openFolderMenu(event, meta.path, folder)
                    : undefined
                }
                onNeed={() => {
                  if (meta?.path && !photos.length) fetchAlbum(meta.path);
                }}
                onMore={() => fetchAlbum(meta.path, Math.min((meta.total || photos.length) + ALBUM_PAGE, 500))}
              />
            ))
        : (
        <>
          {tree.groups.length || tree.ungrouped.length ? (
            <>
              {activeFolder ? (
                <div className="folder-now">
                  <Link
                    to="/photos"
                    className="secondary"
                    onClick={(event) => {
                      event.preventDefault();
                      showAllFolders();
                    }}
                    {...tip("Show every album. Folder View always starts here.")}
                  >
                    All folders
                  </Link>
                  <span
                    className="hint"
                    onContextMenu={(event) => openFolderMenu(event, activePath, activeFolder)}
                  >
                    {albumLabel(activePath, activeFolder)}
                  </span>
                  {activePath ? (
                    <FolderStarButton
                      name={activeFolder}
                      starred={isFolderStarred(openPaths.length === 1 ? openPaths[0] : activePath, starredFolders)}
                      onToggle={() => toggleStar(openPaths.length === 1 ? openPaths[0] : activePath)}
                    />
                  ) : null}
                </div>
              ) : (
                <div className="folder-index">
                  <div className="folder-index-tools">
                    <input
                      type="search"
                      value={folderQuery}
                      onChange={(e) => setFolderQuery(e.target.value)}
                      placeholder="Find a folder"
                      aria-label="Find a folder"
                    />
                    {index.starred.length || index.decades.length > 1 ? (
                      <nav className="folder-decade-nav" aria-label="Jump to starred albums or a decade">
                        {index.starred.length ? (
                          <a className="starred-jump" href="#starred-folders">
                            Starred
                          </a>
                        ) : null}
                        {index.decades.map((decade) => (
                          <a key={decade.label} href={`#year-${decade.year}`}>
                            {decade.label}
                          </a>
                        ))}
                      </nav>
                    ) : null}
                  </div>
                  {index.starred.length ? (
                    <section id="starred-folders" className="folder-year folder-starred">
                      <h2>
                        <span className="folder-starred-title">
                          <svg className="folder-starred-mark" viewBox="0 0 24 24" aria-hidden="true">
                            <path
                              d="M12 3.1 14.7 9h6.6l-5.3 4 2 6.6L12 16.2 5.9 19.6l2-6.6-5.3-4h6.6L12 3.1z"
                              fill="currentColor"
                            />
                          </svg>
                          Starred
                        </span>
                        <span className="hint"> · {countLabel(index.starred.length, "album")}</span>
                      </h2>
                      <p className="folder-starred-lede">Albums kept at the top of Folder View.</p>
                      <div className="folder-index-grid">
                        {index.starred.map((item) => folderTile(item, { showYear: true }))}
                      </div>
                    </section>
                  ) : null}
                  {index.years.map((row) => (
                    <section key={row.year} id={`year-${row.year}`} className="folder-year">
                      <h2>
                        {row.year === "Other" ? "Other albums" : row.year}
                        <span className="hint">
                          {" "}
                          · {countLabel(row.items.length, "album")}
                        </span>
                      </h2>
                      <div className="folder-index-grid">{row.items.map(folderTile)}</div>
                    </section>
                  ))}
                  {folderQuery.trim() && !index.starred.length && !index.years.length ? (
                    <p className="hint">No folder matches “{folderQuery.trim()}”.</p>
                  ) : null}
                </div>
              )}
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
                        className={`person-chip${activeFolder === album.folder ? " active" : ""}${
                          isFolderStarred(album.path, starredFolders) ? " starred" : ""
                        }`}
                        href={`#${folderAnchor(album.folder)}`}
                        onContextMenu={(event) => openFolderMenu(event, album.path, album.folder)}
                        onClick={(event) => {
                          event.preventDefault();
                          openAlbum(album, group);
                        }}
                        {...tip(`Show photos in ${albumLabel(album.path, album.folder)}.`)}
                      >
                        <FolderStarButton
                          name={albumLabel(album.path, album.folder)}
                          starred={isFolderStarred(album.path, starredFolders)}
                          onToggle={() => toggleStar(album.path)}
                        />
                        {albumLabel(album.path, album.folder)}
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
                    label={albumLabel(group.path, group.name)}
                    photos={loadedGroup.items || []}
                    meta={{ path: group.path, total: loadedGroup.total || group.total }}
                    current
                    anchor={false}
                    onSelect={() => setActiveFolder(group.name)}
                    resetting={resetting}
                    onPlay={async () => {
                      const all = preferLargerCopy((await loadAllPhotos({ folder: [group.path] })).items || []);
                      beginPlay(nav, all, { kind: "album", title: albumLabel(group.path, group.name), from: "/photos" });
                    }}
                    onReset={() => askResetMatching(group.name)}
                    onRename={(event) => openFolderMenu(event, group.path, group.name)}
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
                    label={albumLabel(group.path, group.name)}
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
                      beginPlay(nav, all, { kind: "album", title: albumLabel(group.path, group.name), from: "/photos" });
                    }}
                    onReset={() => askResetMatching(group.name)}
                    onRename={(event) => openFolderMenu(event, group.path, group.name)}
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
                      label={albumLabel(album.path, album.folder)}
                      photos={loaded.items || []}
                      meta={{ path: album.path, total: loaded.total || album.photos }}
                      current={activeFolder === album.folder}
                      onSelect={() => setActiveFolder(album.folder)}
                      resetting={resetting}
                      onPlay={async () => {
                        const all = preferLargerCopy((await loadAllPhotos({ folder: [album.path] })).items || []);
                        beginPlay(nav, all, { kind: "album", title: albumLabel(album.path, album.folder), from: "/photos" });
                      }}
                      onReset={() => askResetMatching(album.folder)}
                      onRename={(event) => openFolderMenu(event, album.path, album.folder)}
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
                  label={albumLabel(album.path, album.folder)}
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
                    beginPlay(nav, all, { kind: "album", title: albumLabel(album.path, album.folder), from: "/photos" });
                  }}
                  onReset={() => askResetMatching(album.folder)}
                  onRename={(event) => openFolderMenu(event, album.path, album.folder)}
                  onNeed={() => {
                    if (!(loaded.items || []).length) fetchAlbum(album.path);
                  }}
                  onMore={() => fetchAlbum(album.path, Math.min((loaded.total || album.photos) + ALBUM_PAGE, 500))}
                />
              );
            })}
        </>
      )}
      {folderMenu ? (
        <FolderRenameMenu
          menu={folderMenu}
          current={albumLabel(folderMenu.path, folderMenu.name)}
          original={folderMenu.name}
          onClose={() => setFolderMenu(null)}
          onSave={(draft) => {
            saveFolderName(folderMenu.path, folderMenu.name, draft);
            setFolderMenu(null);
          }}
        />
      ) : null}
    </div>
  );
}

function AlbumBlock({ folder, label, photos, meta, current, onSelect, resetting, onPlay, onReset, onNeed, onMore, onRename, anchor = true }) {
  const [labelsOn] = usePhotoLabels();
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
  const shown = label || folder;
  return (
    <section
      ref={ref}
      className={`folder-block album-block${current ? " current" : ""}`}
      id={anchor ? folderAnchor(folder) : undefined}
      onClick={() => onSelect?.()}
    >
      <header className="album-head" onContextMenu={onRename}>
        <div className="album-head-copy">
          <p className="eyebrow">Album</p>
          <h2>{shown}</h2>
          <p className="album-head-count">
            {total} photo{total === 1 ? "" : "s"}
          </p>
        </div>
        <div className="album-head-actions">
          <NamesToggle />
          <button
            type="button"
            className="secondary"
            onClick={onPlay}
            {...tip(`Play ${shown} in date order. Fullscreen.`)}
          >
            Play
          </button>
          <button
            type="button"
            className="ghost"
            disabled={resetting}
            onClick={onReset}
            {...tip(`Undo auto-matched names in ${shown}. Names you typed stay.`)}
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
              id={`photo-tile-${p.id}`}
              style={{ "--tile-ar": tileAr }}
              onClick={() =>
                writeAlbumPos({
                  hash: folderAnchor(folder),
                  photoId: Number(p.id),
                  path: meta?.path || "",
                  count: photos.length,
                })
              }
            >
              <LabeledPhoto
                photo={p}
                src={p.thumb_url}
                to={photoLink(p.id)}
                toState={{ fullscreen: true, from: `/photos#${folderAnchor(folder)}` }}
                overlayTags={labelsOn}
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

const MENU_PAD = 8;

function ScanConfirm({ paths, titles, onCancel, onConfirm }) {
  const n = (paths || []).length;
  return (
    <ConfirmAsk
      title={n === 1 ? "Find Known Faces in this folder?" : `Find Known Faces in ${n} folders?`}
      body="Read photos that are not in the catalog yet. Already scanned albums stay as they are. Files stay where they are."
      onCancel={onCancel}
      onConfirm={onConfirm}
    >
      <ul className="scan-confirm-list">
        {(paths || []).map((path) => {
          const name = folderDisplayName(path, folderLabel(path), titles);
          const disk = folderLabel(path);
          return (
            <li key={path}>
              <strong>{name}</strong>
              {name !== disk ? <div className="hint">{disk}</div> : null}
              <div className="hint" title={path}>{path}</div>
            </li>
          );
        })}
      </ul>
    </ConfirmAsk>
  );
}

function FolderRenameMenu({ menu, current, original, onClose, onSave }) {
  const box = useRef(null);
  const input = useRef(null);
  const [draft, setDraft] = useState(current || original || "");
  const custom = Boolean(String(current || "").trim() && String(current).trim() !== String(original || "").trim());

  useEffect(() => {
    function insideMenu(event) {
      if (!box.current) return false;
      if (event?.target && box.current.contains(event.target)) return true;
      return false;
    }
    function hide(event) {
      if (event?.type === "keydown") {
        if (event.key !== "Escape") return;
        onClose();
        return;
      }
      if (insideMenu(event)) return;
      onClose();
    }
    window.addEventListener("pointerdown", hide, true);
    window.addEventListener("keydown", hide);
    window.addEventListener("scroll", hide, true);
    return () => {
      window.removeEventListener("pointerdown", hide, true);
      window.removeEventListener("keydown", hide);
      window.removeEventListener("scroll", hide, true);
    };
  }, [onClose]);

  useLayoutEffect(() => {
    if (!box.current) return;
    const r = box.current.getBoundingClientRect();
    let left = menu.x;
    let top = menu.y;
    if (left + r.width > window.innerWidth - MENU_PAD) left = window.innerWidth - r.width - MENU_PAD;
    if (top + r.height > window.innerHeight - MENU_PAD) top = window.innerHeight - r.height - MENU_PAD;
    if (left < MENU_PAD) left = MENU_PAD;
    if (top < MENU_PAD) top = MENU_PAD;
    box.current.style.left = `${left}px`;
    box.current.style.top = `${top}px`;
    input.current?.focus();
    input.current?.select();
  }, [menu]);

  function submit() {
    onSave(draft);
  }

  return (
    <div
      ref={box}
      className="photo-menu"
      role="menu"
      aria-label="Rename album"
      onPointerDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <div className="photo-menu-note">Rename in Folder View. The folder on disk is not renamed.</div>
      <input
        ref={input}
        className="photo-menu-input"
        type="text"
        maxLength={FOLDER_TITLE_MAX}
        value={draft}
        placeholder={original || "Album name"}
        aria-label="Album name"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            submit();
          }
        }}
      />
      <button type="button" role="menuitem" onClick={submit}>
        Save name
      </button>
      {custom ? (
        <button type="button" role="menuitem" onClick={() => onSave("")}>
          Use folder name
        </button>
      ) : null}
    </div>
  );
}
