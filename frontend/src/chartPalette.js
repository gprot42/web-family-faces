// Colour schemes for the family tree's fan chart. Each maps an angle across
// the half disc (π on the left, father's side, to 0 on the right, mother's)
// to a hue; lightness by generation is applied in familyChart.js.
export const PALETTE_KEY = "photosort-fan-palette";

function ramp(theta, leftFar, leftNear, rightNear, rightFar) {
  const half = Math.PI / 2;
  if (theta >= half) return leftNear + ((theta - half) / half) * (leftFar - leftNear);
  return rightFar + (theta / half) * (rightNear - rightFar);
}

export const PALETTES = [
  {
    id: "earth",
    label: "Earth",
    hint: "Terracotta and ochre for the father's side, sage and teal for the mother's. The app's own look.",
    swatch: ["#d9a48a", "#e0c48f", "#b9cf9e", "#9fd3c5"],
    hue: (t) => ramp(t, 16, 38, 95, 175),
    sat: 46,
  },
  {
    id: "rainbow",
    label: "Rainbow",
    hint: "Green on the left through blue to pink on the right, the classic fan chart look.",
    swatch: ["#b6e2b6", "#b3d4ef", "#c9b8ef", "#f1b6d3"],
    hue: (t) => 120 + (1 - t / Math.PI) * 200,
    sat: 55,
  },
  {
    id: "ocean",
    label: "Ocean",
    hint: "Deep blue to violet on the father's side, teal to green on the mother's.",
    swatch: ["#a9bfe8", "#c3b3e6", "#a5d8d3", "#b8dcb0"],
    hue: (t) => ramp(t, 225, 265, 175, 120),
    sat: 48,
  },
  {
    id: "sunset",
    label: "Sunset",
    hint: "Red and orange on the father's side, plum and rose on the mother's.",
    swatch: ["#e8a5a0", "#f0c39a", "#d7b0d8", "#f0b4c8"],
    hue: (t) => ramp(t, 5, 32, 290, 335),
    sat: 52,
  },
  {
    id: "mono",
    label: "Single tone",
    hint: "One warm tone, paler with every generation. Lineage is told by position alone.",
    swatch: ["#d7b39f", "#e2c7b7", "#ecdacf", "#f4ebe4"],
    hue: () => 22,
    sat: 30,
  },
];

export function readPalette() {
  try {
    const value = localStorage.getItem(PALETTE_KEY);
    if (PALETTES.some((p) => p.id === value)) return value;
  } catch {
    /* private mode */
  }
  return "earth";
}

export function savePalette(id) {
  const palette = PALETTES.some((p) => p.id === id) ? id : "earth";
  try {
    localStorage.setItem(PALETTE_KEY, palette);
  } catch {
    /* private mode */
  }
  return palette;
}

export function paletteById(id) {
  return PALETTES.find((p) => p.id === id) || PALETTES[0];
}
