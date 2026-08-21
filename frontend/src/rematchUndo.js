const KEY = "photosort-rematch-undo";

export function readRematchUndo(photoId) {
  if (photoId == null || photoId === "") return null;
  try {
    const raw = JSON.parse(sessionStorage.getItem(KEY) || "null");
    if (!raw || Number(raw.photoId) !== Number(photoId)) return null;
    const faceIds = [...new Set((raw.faceIds || []).map(Number).filter((id) => id > 0))];
    if (!faceIds.length) return null;
    return { photoId: Number(raw.photoId), faceIds, names: (raw.names || []).filter(Boolean) };
  } catch {
    return null;
  }
}

export function writeRematchUndo(photoId, faceIds, names = []) {
  const ids = [...new Set((faceIds || []).map(Number).filter((id) => id > 0))];
  if (photoId == null || photoId === "" || !ids.length) {
    clearRematchUndo(photoId);
    return null;
  }
  const payload = {
    photoId: Number(photoId),
    faceIds: ids,
    names: [...new Set((names || []).filter(Boolean))],
  };
  try {
    sessionStorage.setItem(KEY, JSON.stringify(payload));
  } catch {
    /* ignore */
  }
  return payload;
}

export function clearRematchUndo(photoId) {
  try {
    if (photoId != null && photoId !== "") {
      const raw = JSON.parse(sessionStorage.getItem(KEY) || "null");
      if (raw && Number(raw.photoId) !== Number(photoId)) return;
    }
    sessionStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
