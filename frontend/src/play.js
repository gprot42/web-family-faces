export const PLAY_KEY = "photosort-play";
export const PLAY_EVENT = "photosort-play";
export const PLAY_INTERVAL_KEY = "photosort-play-interval-ms";
export const PLAY_INTERVAL_MS = 2500;
export const PLAY_INTERVAL_MIN_MS = 1000;
export const PLAY_INTERVAL_MAX_MS = 30000;

export function clampPlayIntervalMs(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n)) return PLAY_INTERVAL_MS;
  return Math.min(PLAY_INTERVAL_MAX_MS, Math.max(PLAY_INTERVAL_MIN_MS, Math.round(n)));
}

export function readPlayIntervalMs() {
  try {
    const raw = localStorage.getItem(PLAY_INTERVAL_KEY);
    if (raw == null || raw === "") return PLAY_INTERVAL_MS;
    return clampPlayIntervalMs(raw);
  } catch {
    return PLAY_INTERVAL_MS;
  }
}

export function applyPlayIntervalMs(ms) {
  const next = clampPlayIntervalMs(ms);
  try {
    localStorage.setItem(PLAY_INTERVAL_KEY, String(next));
  } catch {
    /* private mode */
  }
  const session = readPlay();
  if (session) updatePlay({ intervalMs: next });
  return next;
}

export function uniquePhotoIds(photos) {
  const ids = [];
  const seen = new Set();
  for (const item of photos || []) {
    const id = Number(item?.photo_id || item?.id);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return ids;
}

export function enterBrowserFullscreen(el = document.documentElement) {
  const node = el || document.documentElement;
  const req = node.requestFullscreen || node.webkitRequestFullscreen;
  if (!req) return Promise.resolve();
  return Promise.resolve(req.call(node)).catch(() => {});
}

export function exitBrowserFullscreen() {
  const doc = document;
  if (!doc.fullscreenElement && !doc.webkitFullscreenElement) return Promise.resolve();
  const exit = doc.exitFullscreen || doc.webkitExitFullscreen;
  if (!exit) return Promise.resolve();
  return Promise.resolve(exit.call(doc)).catch(() => {});
}

export function readPlay() {
  try {
    const raw = sessionStorage.getItem(PLAY_KEY);
    if (!raw) return null;
    const data = JSON.parse(raw);
    if (!data || !Array.isArray(data.ids) || !data.ids.length) return null;
    data.ids = data.ids.map(Number).filter(Boolean);
    return data.ids.length ? data : null;
  } catch {
    return null;
  }
}

export function writePlay(session) {
  const next = session && session.ids?.length ? session : null;
  try {
    if (next) sessionStorage.setItem(PLAY_KEY, JSON.stringify(next));
    else sessionStorage.removeItem(PLAY_KEY);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(PLAY_EVENT));
  return next;
}

export function updatePlay(patch) {
  const cur = readPlay();
  if (!cur) return null;
  return writePlay({ ...cur, ...patch });
}

export function stopPlay() {
  return writePlay(null);
}

export function playHref(photoId, session) {
  if (!photoId) return "/photos";
  if (session?.personId) return `/photos/${photoId}?person=${session.personId}`;
  if (session?.tag) return `/photos/${photoId}?tag=${encodeURIComponent(session.tag)}`;
  return `/photos/${photoId}`;
}

export function playIndexOf(session, photoId) {
  if (!session?.ids?.length) return -1;
  const cur = Number(photoId);
  return session.ids.findIndex((item) => Number(item) === cur);
}

export function prefetchPlay(session, photoId) {
  const at = playIndexOf(session, photoId);
  if (at < 0) return;
  for (const nextId of [session.ids[at + 1], session.ids[at + 2]]) {
    if (!nextId) continue;
    const thumb = new Image();
    thumb.src = `/api/photos/${nextId}/thumb`;
    const view = new Image();
    view.src = `/api/photos/${nextId}/view`;
  }
}

export function beginPlay(nav, photos, opts = {}) {
  const ids = uniquePhotoIds(photos);
  if (!ids.length) return false;
  const start = Math.max(0, ids.indexOf(Number(opts.startId)) >= 0 ? ids.indexOf(Number(opts.startId)) : 0);
  const session = writePlay({
    ids,
    index: start,
    kind: opts.kind || "album",
    title: opts.title || "",
    personId: opts.personId != null ? String(opts.personId) : null,
    tag: opts.tag ? String(opts.tag) : null,
    playing: true,
    intervalMs: opts.intervalMs || readPlayIntervalMs(),
  });
  const firstThumb = new Image();
  firstThumb.src = `/api/photos/${ids[start]}/thumb`;
  const firstView = new Image();
  firstView.src = `/api/photos/${ids[start]}/view`;
  prefetchPlay(session, ids[start]);
  enterBrowserFullscreen();
  nav(playHref(ids[start], session), { state: { fullscreen: true, from: opts.from } });
  return true;
}
