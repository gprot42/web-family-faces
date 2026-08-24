export const PHOTO_CHANGE_EVENT = "photosort-photo-change";
export const CATALOG_CHANGE_EVENT = "photosort-catalog-change";

export function emitPhotoChange(photo) {
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
