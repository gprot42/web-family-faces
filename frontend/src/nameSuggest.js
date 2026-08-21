function tokens(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

export function scoreName(query, name) {
  const q = String(query || "").trim().toLowerCase();
  const n = String(name || "").trim().toLowerCase();
  if (q.length < 2 || !n || n === q) return 0;
  const qParts = tokens(q);
  const nParts = tokens(n);
  if (!qParts.length || !nParts.length) return 0;
  const prefixEvery = qParts.every((qp) => nParts.some((np) => np.startsWith(qp)));
  const contains = n.includes(q);
  if (!prefixEvery && !contains) return 0;
  let score = 0;
  if (n.startsWith(q)) score += 100;
  if (nParts[0].startsWith(qParts[0])) score += 40;
  if (prefixEvery) score += 30;
  else if (contains) score += 8;
  if (nParts.some((np) => np === qParts[0])) score += 12;
  score -= Math.min(12, Math.max(0, nParts.length - qParts.length));
  return score;
}

export function matchPeople(query, people, { excludeId, limit = 6 } = {}) {
  const q = String(query || "").trim();
  if (q.length < 2) return [];
  const scored = [];
  for (const person of people || []) {
    if (person?.unknown_name) continue;
    if (excludeId != null && String(person.id) === String(excludeId)) continue;
    const name = String(person.name || "").trim();
    const nick = String(person.nickname || "").trim();
    const score = Math.max(scoreName(q, name), nick ? scoreName(q, nick) + 4 : 0);
    if (score > 0) scored.push({ person, score });
  }
  scored.sort((a, b) => b.score - a.score || String(a.person.name).localeCompare(String(b.person.name)));
  return scored.slice(0, limit).map((row) => row.person);
}

export function uniqueFirstName(query, people, { excludeId } = {}) {
  const q = String(query || "").trim().toLowerCase();
  if (q.length < 2) return null;
  const hits = [];
  for (const person of people || []) {
    if (person?.unknown_name) continue;
    if (excludeId != null && String(person.id) === String(excludeId)) continue;
    const first = tokens(person.name)[0] || "";
    const nickFirst = tokens(String(person.nickname || "").split(/[,;/]/)[0])[0] || "";
    if (first === q || nickFirst === q) hits.push(person);
  }
  return hits.length === 1 ? hits[0] : null;
}

export function splitNameMatch(query, name) {
  const raw = String(name || "");
  const q = String(query || "").trim();
  if (q && raw.toLowerCase().startsWith(q.toLowerCase())) {
    return { head: raw.slice(0, q.length), rest: raw.slice(q.length) };
  }
  return { head: "", rest: raw };
}
