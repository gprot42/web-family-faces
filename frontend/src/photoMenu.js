export const PHOTO_CHANGE_EVENT = "photosort-photo-change";
export const CATALOG_CHANGE_EVENT = "photosort-catalog-change";

const rotationById = new Map();

export function rememberPhotoRotation(id, rotation) {
  const pid = Number(id);
  if (!Number.isFinite(pid) || pid <= 0) return;
  const rot = (((Number(rotation) || 0) % 360) + 360) % 360;
  rotationById.set(pid, rot);
}

export function readPhotoRotation(id) {
  const pid = Number(id);
  if (!Number.isFinite(pid) || !rotationById.has(pid)) return null;
  return rotationById.get(pid);
}

export function emitPhotoChange(photo) {
  if (photo?.id != null && photo.rotation != null) rememberPhotoRotation(photo.id, photo.rotation);
  window.dispatchEvent(new CustomEvent(PHOTO_CHANGE_EVENT, { detail: photo }));
}

export function emitCatalogChange() {
  window.dispatchEvent(new Event(CATALOG_CHANGE_EVENT));
}

let openMenu = null;

export function showPhotoMenu(event, photo) {
  if (!photo?.id || !openMenu) return;
  event.preventDefault();
  event.stopPropagation();
  const hit = event.target?.closest?.("[data-face-id]");
  const faceId = hit ? Number(hit.getAttribute("data-face-id")) : NaN;
  openMenu({
    x: event.clientX,
    y: event.clientY,
    photo,
    faceId: Number.isFinite(faceId) ? faceId : null,
  });
}

export function subscribePhotoMenu(fn) {
  openMenu = fn;
  return () => {
    if (openMenu === fn) openMenu = null;
  };
}
