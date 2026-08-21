export const NAMETAG_KEY = "photosort-nametag";
export const NAMETAG_EVENT = "photosort-nametag";

export const NAMETAG_PLACEMENTS = [
  {
    id: "on",
    label: "On the person",
    hint: "Name sits on the face.",
  },
  {
    id: "above",
    label: "Above the head",
    hint: "Name sits just above the head.",
  },
  {
    id: "below",
    label: "Below the body",
    hint: "Name sits under the person.",
  },
];

export function readNametag() {
  try {
    const value = localStorage.getItem(NAMETAG_KEY);
    if (NAMETAG_PLACEMENTS.some((item) => item.id === value)) return value;
  } catch {
    /* private mode */
  }
  return "below";
}

export function applyNametag(id) {
  const place = NAMETAG_PLACEMENTS.some((item) => item.id === id) ? id : "below";
  document.documentElement.setAttribute("data-nametag", place);
  try {
    localStorage.setItem(NAMETAG_KEY, place);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(NAMETAG_EVENT));
  return place;
}

export const FULLSCREEN_LABELS_KEY = "photosort-show-names";
export const FULLSCREEN_LABELS_SESSION_KEY = "photosort-show-names-session";
export const FULLSCREEN_LABELS_EVENT = "photosort-fullscreen-labels";

export function readFullscreenLabels() {
  try {
    const session = sessionStorage.getItem(FULLSCREEN_LABELS_SESSION_KEY);
    if (session === "0" || session === "false") return false;
    if (session === "1" || session === "true") return true;
    const value = localStorage.getItem(FULLSCREEN_LABELS_KEY);
    if (value === "0" || value === "false") return false;
  } catch {
    /* private mode */
  }
  return true;
}

export function applyFullscreenLabels(on) {
  const next = !!on;
  try {
    sessionStorage.setItem(FULLSCREEN_LABELS_SESSION_KEY, next ? "1" : "0");
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(FULLSCREEN_LABELS_EVENT));
  return next;
}

export function applyFullscreenLabelsDefault(on) {
  const next = !!on;
  try {
    localStorage.setItem(FULLSCREEN_LABELS_KEY, next ? "1" : "0");
    sessionStorage.removeItem(FULLSCREEN_LABELS_SESSION_KEY);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(FULLSCREEN_LABELS_EVENT));
  return next;
}
