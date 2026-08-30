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

export const LABEL_LAYOUT_KEY = "photosort-label-layout";
export const LABEL_LAYOUT_EVENT = "photosort-label-layout";

export const LABEL_LAYOUTS = [
  {
    id: "smart",
    label: "Smart",
    hint: "Numbers and names sit above the head, not on the eyes.",
  },
  {
    id: "rows",
    label: "Rows",
    hint: "Back row names go above; front row names go below.",
  },
  {
    id: "halo",
    label: "Halo",
    hint: "Park names around the group, not on people.",
  },
  {
    id: "numbers",
    label: "Numbers",
    hint: "Numbers on faces. Full names stay in the list.",
  },
];

export function readLabelLayout() {
  try {
    const value = localStorage.getItem(LABEL_LAYOUT_KEY);
    if (LABEL_LAYOUTS.some((item) => item.id === value)) return value;
  } catch {
    /* private mode */
  }
  return "smart";
}

export function applyLabelLayout(id) {
  const layout = LABEL_LAYOUTS.some((item) => item.id === id) ? id : "smart";
  try {
    localStorage.setItem(LABEL_LAYOUT_KEY, layout);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(LABEL_LAYOUT_EVENT));
  return layout;
}

export function cycleLabelLayout() {
  const cur = readLabelLayout();
  const i = Math.max(0, LABEL_LAYOUTS.findIndex((item) => item.id === cur));
  return applyLabelLayout(LABEL_LAYOUTS[(i + 1) % LABEL_LAYOUTS.length].id);
}

export function labelLayoutInfo(id = readLabelLayout()) {
  return LABEL_LAYOUTS.find((item) => item.id === id) || LABEL_LAYOUTS[0];
}

export const LABEL_SIZE_KEY = "photosort-label-size";
export const LABEL_SIZE_EVENT = "photosort-label-size";

export const LABEL_SIZES = [
  {
    id: "small",
    label: "Small",
    hint: "Compact chips. Best on crowded group photos.",
    scale: 0.72,
  },
  {
    id: "medium",
    label: "Medium",
    hint: "A bit smaller than the old labels. Easy to read, less of the faces.",
    scale: 0.86,
  },
  {
    id: "large",
    label: "Large",
    hint: "Bigger name chips. Use when a photo has only a few people.",
    scale: 1,
  },
];

export function readLabelSize() {
  try {
    const value = localStorage.getItem(LABEL_SIZE_KEY);
    if (LABEL_SIZES.some((item) => item.id === value)) return value;
  } catch {
    /* private mode */
  }
  return "small";
}

export function applyLabelSize(id) {
  const size = LABEL_SIZES.some((item) => item.id === id) ? id : "small";
  document.documentElement.setAttribute("data-label-size", size);
  try {
    localStorage.setItem(LABEL_SIZE_KEY, size);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(LABEL_SIZE_EVENT));
  return size;
}

export function cycleLabelSize() {
  const cur = readLabelSize();
  const i = Math.max(0, LABEL_SIZES.findIndex((item) => item.id === cur));
  return applyLabelSize(LABEL_SIZES[(i + 1) % LABEL_SIZES.length].id);
}

export function labelSizeInfo(id = readLabelSize()) {
  return LABEL_SIZES.find((item) => item.id === id) || LABEL_SIZES[0];
}

export const LABEL_STYLE_KEY = "photosort-label-style";
export const LABEL_STYLE_EVENT = "photosort-label-style";

export const LABEL_STYLES = [
  {
    id: "pill",
    label: "Pill",
    hint: "Filled capsule with a number and name.",
  },
  {
    id: "outline",
    label: "Outline",
    hint: "Thin bordered chip. More of the photo shows through.",
  },
  {
    id: "shadow",
    label: "Shadow",
    hint: "Name only, with a dark outline. No chip.",
  },
];

export function readLabelStyle() {
  try {
    const value = localStorage.getItem(LABEL_STYLE_KEY);
    if (LABEL_STYLES.some((item) => item.id === value)) return value;
  } catch {
    /* private mode */
  }
  return "pill";
}

export function applyLabelStyle(id) {
  const style = LABEL_STYLES.some((item) => item.id === id) ? id : "pill";
  document.documentElement.setAttribute("data-label-style", style);
  try {
    localStorage.setItem(LABEL_STYLE_KEY, style);
  } catch {
    /* private mode */
  }
  window.dispatchEvent(new Event(LABEL_STYLE_EVENT));
  return style;
}

export function cycleLabelStyle() {
  const cur = readLabelStyle();
  const i = Math.max(0, LABEL_STYLES.findIndex((item) => item.id === cur));
  return applyLabelStyle(LABEL_STYLES[(i + 1) % LABEL_STYLES.length].id);
}

export function labelStyleInfo(id = readLabelStyle()) {
  return LABEL_STYLES.find((item) => item.id === id) || LABEL_STYLES[0];
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
