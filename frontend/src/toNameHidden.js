const KEY = "photosort-to-name-hidden-people";

function cleanIds(ids) {
  return [...new Set((ids || []).map(Number).filter((n) => Number.isFinite(n) && n > 0))];
}

export function readHiddenPeople() {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(raw) ? cleanIds(raw) : [];
  } catch {
    return [];
  }
}

export function writeHiddenPeople(ids) {
  const next = cleanIds(ids);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* private mode */
  }
  return next;
}

export function hidePerson(id) {
  const n = Number(id);
  const ids = readHiddenPeople();
  if (!n || ids.includes(n)) return ids;
  return writeHiddenPeople([...ids, n]);
}

export function showPerson(id) {
  const n = Number(id);
  return writeHiddenPeople(readHiddenPeople().filter((item) => item !== n));
}
