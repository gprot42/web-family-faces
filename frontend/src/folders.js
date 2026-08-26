export const FOLDERS_KEY = "photosort-import-folders";
export const STARRED_FOLDERS_KEY = "photosort-starred-folders";
export const FOLDER_TITLES_KEY = "photosort-folder-titles";
export const FOLDER_TITLE_MAX = 80;
export const ALL_FOLDERS_EVENT = "photosort-all-folders";

export function requestFolderIndex() {
  window.dispatchEvent(new Event(ALL_FOLDERS_EVENT));
}

export function isAlbumPath(path) {
  const raw = String(path || "").trim();
  if (!raw) return false;
  const n = normalizeFolderPath(raw).toLowerCase();
  return n !== "volumes" && n !== "/volumes";
}

export function folderLabel(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  return parts[parts.length - 1] || path;
}

/** Album folder that contains a photo file. */
export function photoAlbumName(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  if (parts.length < 2) return "";
  return parts[parts.length - 2];
}

/** Last two path segments so “Old” is not shown without its parent album. */
export function folderBreadcrumb(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  if (parts.length >= 2) return `${parts[parts.length - 2]} / ${parts[parts.length - 1]}`;
  return parts[0] || path;
}

export function normalizeFolderPath(path) {
  const raw = String(path || "").trim();
  if (!raw || raw === "/") return "/";
  return raw.replace(/\/+$/, "") || "/";
}

/** True when `child` is the same folder as `parent`, or a folder inside it. */
export function isSameOrInside(child, parent) {
  const a = normalizeFolderPath(child);
  const b = normalizeFolderPath(parent);
  if (b === "/") return a === "/" || a.startsWith("/");
  return a === b || a.startsWith(`${b}/`);
}

export function isInside(child, parent) {
  const a = normalizeFolderPath(child);
  const b = normalizeFolderPath(parent);
  if (b === "/") return a !== "/";
  return a.startsWith(`${b}/`);
}

/**
 * How a folder relates to the current selection.
 * - all: this exact folder is ticked
 * - partial: only albums inside it are ticked
 * - none: nothing under it is selected
 */
export function folderSelectionState(path, picked) {
  const items = (picked || []).filter(Boolean);
  const exact = items.some((item) => normalizeFolderPath(item) === normalizeFolderPath(path));
  const inside = items.filter((item) => isInside(item, path)).length;
  if (exact) return { state: "all", inside };
  if (inside) return { state: "partial", inside };
  return { state: "none", inside: 0 };
}

/** Add or remove one folder. Never drops other selected albums. */
export function toggleAlbumPath(path, picked) {
  const items = (picked || []).filter(isAlbumPath);
  if (!isAlbumPath(path)) return items;
  const n = normalizeFolderPath(path);
  const has = items.some((item) => normalizeFolderPath(item) === n);
  if (has) return items.filter((item) => normalizeFolderPath(item) !== n);
  return [...items, path];
}

export function importFoldersStored() {
  try {
    const raw = localStorage.getItem(FOLDERS_KEY);
    if (raw == null) return false;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.filter(Boolean).length === 0) return false;
    return true;
  } catch {
    return false;
  }
}

export function readImportFolders(fallback) {
  try {
    const raw = localStorage.getItem(FOLDERS_KEY);
    if (raw == null) return fallback ? [fallback] : [];
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed)) {
      const items = parsed.filter(Boolean);
      if (items.length) return items;
    }
  } catch {
    /* ignore */
  }
  return fallback ? [fallback] : [];
}

/** True when this photo file sits in one of the selected albums. */
export function photoInFolders(photoPath, folders) {
  const items = (folders || []).filter(Boolean);
  if (!items.length) return false;
  return items.some((folder) => isSameOrInside(photoPath, folder));
}

/** True when the catalog already has photos in this album (or a folder inside it). */
export function folderIsIndexed(path, photos, catalog) {
  if (!path) return false;
  if ((photos || []).some((item) => photoInFolders(item.path || item, [path]))) return true;
  const label = folderLabel(path);
  return (catalog || []).some((item) => {
    const itemPath = item?.path || "";
    if (itemPath && (isSameOrInside(itemPath, path) || isSameOrInside(path, itemPath))) return true;
    return item?.folder === label;
  });
}

/** Folders in `next` that were not already in `prev`. */
export function addedFolderPaths(next, prev) {
  const before = new Set((prev || []).filter(isAlbumPath).map(normalizeFolderPath));
  return [...new Set((next || []).filter(isAlbumPath))].filter(
    (path) => !before.has(normalizeFolderPath(path)),
  );
}

export function writeImportFolders(folders) {
  try {
    localStorage.setItem(FOLDERS_KEY, JSON.stringify((folders || []).filter(Boolean)));
  } catch {
    /* ignore */
  }
}

export function readStarredFolders() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STARRED_FOLDERS_KEY) || "[]");
    if (!Array.isArray(parsed)) return [];
    const seen = new Set();
    const out = [];
    for (const item of parsed) {
      const path = normalizeFolderPath(item);
      if (!path || path === "/" || seen.has(path)) continue;
      seen.add(path);
      out.push(path);
    }
    return out;
  } catch {
    return [];
  }
}

export function writeStarredFolders(folders) {
  try {
    const seen = new Set();
    const out = [];
    for (const item of folders || []) {
      const path = normalizeFolderPath(item);
      if (!path || path === "/" || seen.has(path)) continue;
      seen.add(path);
      out.push(path);
    }
    localStorage.setItem(STARRED_FOLDERS_KEY, JSON.stringify(out));
  } catch {
    /* ignore */
  }
}

export function isFolderStarred(path, starred) {
  const n = normalizeFolderPath(path);
  if (!n || n === "/") return false;
  return (starred || []).some((item) => normalizeFolderPath(item) === n);
}

/** Add or remove a folder star. Newly starred folders go at the end. */
export function toggleStarredFolder(path, starred) {
  const n = normalizeFolderPath(path);
  if (!n || n === "/") return [...(starred || [])];
  const items = [];
  const seen = new Set();
  for (const item of starred || []) {
    const key = normalizeFolderPath(item);
    if (!key || key === "/" || seen.has(key)) continue;
    seen.add(key);
    items.push(key);
  }
  if (seen.has(n)) return items.filter((item) => item !== n);
  return [...items, n];
}

export function readFolderTitles() {
  try {
    const parsed = JSON.parse(localStorage.getItem(FOLDER_TITLES_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out = {};
    for (const [path, title] of Object.entries(parsed)) {
      const key = normalizeFolderPath(path);
      const name = String(title || "").trim().slice(0, FOLDER_TITLE_MAX);
      if (key && key !== "/" && name) out[key] = name;
    }
    return out;
  } catch {
    return {};
  }
}

export function writeFolderTitles(titles) {
  try {
    const cleaned = {};
    for (const [path, title] of Object.entries(titles || {})) {
      const key = normalizeFolderPath(path);
      const name = String(title || "").trim().slice(0, FOLDER_TITLE_MAX);
      if (key && key !== "/" && name) cleaned[key] = name;
    }
    localStorage.setItem(FOLDER_TITLES_KEY, JSON.stringify(cleaned));
  } catch {
    /* ignore */
  }
}

/** Catalog name for Folder View. The folder on disk is not renamed. */
export function folderDisplayName(path, fallback, titles) {
  const key = normalizeFolderPath(path);
  const custom = key && titles ? String(titles[key] || "").trim() : "";
  return custom || String(fallback || folderLabel(path) || "").trim();
}

export function setFolderTitle(path, title, titles) {
  const key = normalizeFolderPath(path);
  const next = { ...(titles || {}) };
  if (!key || key === "/") return next;
  const name = String(title || "").trim().slice(0, FOLDER_TITLE_MAX);
  if (!name) delete next[key];
  else next[key] = name;
  return next;
}
