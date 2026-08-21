export const FOLDERS_KEY = "photosort-import-folders";

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
