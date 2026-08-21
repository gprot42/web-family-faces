export const PEOPLE_CACHE_KEY = "photosort-people-lite-v2";

export function loadCachedPeople(folder) {
  try {
    const raw = JSON.parse(sessionStorage.getItem(PEOPLE_CACHE_KEY) || "null");
    if (!raw || raw.folder !== (folder || "") || !Array.isArray(raw.items)) return [];
    return raw.items;
  } catch {
    return [];
  }
}

export function saveCachedPeople(folder, items) {
  try {
    sessionStorage.setItem(PEOPLE_CACHE_KEY, JSON.stringify({ folder: folder || "", items }));
  } catch {
    /* ignore quota */
  }
}

export function clearPeopleCache() {
  try {
    sessionStorage.removeItem(PEOPLE_CACHE_KEY);
  } catch {
    /* ignore */
  }
}

export function patchCachedPerson(id, fields, folder = "") {
  const items = loadCachedPeople(folder);
  if (!items.length) return;
  saveCachedPeople(
    folder,
    items.map((p) => (Number(p.id) === Number(id) ? { ...p, ...fields } : p)),
  );
}
