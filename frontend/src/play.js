export const PLAY_KEY = "photosort-play";
export const PLAY_EVENT = "photosort-play";
export const PLAY_INTERVAL_MS = 5000;

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
    const img = new Image();
    img.src = `/api/photos/${nextId}/thumb`;
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
    intervalMs: opts.intervalMs || PLAY_INTERVAL_MS,
  });
  const first = new Image();
  first.src = `/api/photos/${ids[start]}/thumb`;
  prefetchPlay(session, ids[start]);
  enterBrowserFullscreen();
  nav(playHref(ids[start], session), { state: { fullscreen: true, from: opts.from } });
  return true;
}
