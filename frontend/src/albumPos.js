const KEY = "photosort-album-pos";

export function readAlbumPos() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (!raw || typeof raw !== "object") return null;
    return raw;
  } catch {
    return null;
  }
}

export function writeAlbumPos(pos) {
  try {
    if (!pos) {
      sessionStorage.removeItem(KEY);
      return;
    }
    const prev = readAlbumPos() || {};
    sessionStorage.setItem(KEY, JSON.stringify({ ...prev, ...pos }));
  } catch {
    /* private mode */
  }
}

export function clearAlbumPos() {
  writeAlbumPos(null);
}

export function folderHashFrom(from) {
  if (typeof from !== "string") return "";
  const at = from.indexOf("#");
  if (at < 0) return "";
  return from.slice(at + 1);
}

const PERSON_KEY = "photosort-person-pos";

export function personIdFrom(from) {
  if (typeof from !== "string") return 0;
  const path = from.split(/[?#]/)[0];
  const match = path.match(/^\/people\/(\d+)$/);
  return match ? Number(match[1]) : 0;
}

export function personShotHash(photoId) {
  const id = Number(photoId);
  return id ? `photo-tile-${id}` : "";
}

export function clusterHash(clusterId) {
  const id = Number(clusterId);
  return id ? `cluster-${id}` : "";
}

export function clusterIdFrom(from) {
  if (typeof from !== "string") return 0;
  const at = from.indexOf("#");
  const hash = at >= 0 ? from.slice(at + 1) : from.replace(/^#/, "");
  const match = String(hash || "").match(/^cluster-(\d+)/);
  return match ? Number(match[1]) : 0;
}

export function readPersonPos() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(PERSON_KEY) || "null");
    if (!raw || typeof raw !== "object") return null;
    return raw;
  } catch {
    return null;
  }
}

export function writePersonPos(pos) {
  try {
    if (!pos) {
      sessionStorage.removeItem(PERSON_KEY);
      return;
    }
    const prev = readPersonPos() || {};
    sessionStorage.setItem(PERSON_KEY, JSON.stringify({ ...prev, ...pos }));
  } catch {
    /* private mode */
  }
}

export function clearPersonPos() {
  writePersonPos(null);
}
