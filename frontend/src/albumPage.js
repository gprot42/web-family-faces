export const ALBUM_PAGE = 500;
export const ALBUM_PAGE_MIN = 50;
export const ALBUM_PAGE_MAX = 500;
export const ALBUM_PAGE_KEY = "photosort-album-page";
export const ALBUM_PAGE_EVENT = "photosort-album-page";

export function clampAlbumPage(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return ALBUM_PAGE;
  return Math.min(ALBUM_PAGE_MAX, Math.max(ALBUM_PAGE_MIN, Math.round(v)));
}

export function readAlbumPage() {
  try {
    const raw = localStorage.getItem(ALBUM_PAGE_KEY);
    if (raw == null || raw === "") return ALBUM_PAGE;
    return clampAlbumPage(raw);
  } catch {
    return ALBUM_PAGE;
  }
}

export function applyAlbumPage(n) {
  const next = clampAlbumPage(n);
  try {
    localStorage.setItem(ALBUM_PAGE_KEY, String(next));
  } catch {
    /* private mode */
  }
  try {
    window.dispatchEvent(new Event(ALBUM_PAGE_EVENT));
  } catch {
    /* node tests */
  }
  return next;
}

export function albumHasMore(meta, photos) {
  if (!meta?.path || !(photos || []).length) return false;
  const fetched = Number(meta.fetched);
  const apiTotal = Number(meta.apiTotal);
  if (Number.isFinite(fetched) && Number.isFinite(apiTotal)) {
    return fetched < apiTotal;
  }
  return (Number(meta.total) || 0) > photos.length;
}

export function nextAlbumOffset(album) {
  const fetched = Number(album?.fetched);
  if (Number.isFinite(fetched) && fetched >= 0) return fetched;
  return (album?.items || []).length;
}
