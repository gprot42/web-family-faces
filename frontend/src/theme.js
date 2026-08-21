export const THEME_KEY = "photosort-theme";

export const THEMES = [
  {
    id: "present",
    label: "Present",
    hint: "Warm paper, the look you have now.",
    swatch: ["#f3eee6", "#c96b4a", "#5b8f78", "#2b251f"],
  },
  {
    id: "light",
    label: "Light",
    hint: "Clean white pages.",
    swatch: ["#f6f7f9", "#3b6fd4", "#2f8f6b", "#1c1e24"],
  },
  {
    id: "tokyo-night",
    label: "Tokyo Night",
    hint: "Dark blue night.",
    swatch: ["#1a1b26", "#7aa2f7", "#bb9af7", "#c0caf5"],
  },
];

export function readTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    if (THEMES.some((theme) => theme.id === value)) return value;
  } catch {
    /* private mode */
  }
  return "present";
}

export function applyTheme(id) {
  const theme = THEMES.some((item) => item.id === id) ? id : "present";
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.style.colorScheme = theme === "tokyo-night" ? "dark" : "light";
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private mode */
  }
  return theme;
}
