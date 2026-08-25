function tokens(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean);
}

function editDistance(left, right) {
  if (left === right) return 0;
  if (Math.abs(left.length - right.length) > 1) return 2;
  const prev = Array.from({ length: right.length + 1 }, (_, i) => i);
  for (let i = 1; i <= left.length; i += 1) {
    let diag = prev[0];
    prev[0] = i;
    for (let j = 1; j <= right.length; j += 1) {
      const next = left[i - 1] === right[j - 1] ? diag : Math.min(diag, prev[j], prev[j - 1]) + 1;
      diag = prev[j];
      prev[j] = next;
    }
  }
  return prev[right.length];
}

function tokenMatches(word, token) {
  if (!token || !word) return false;
  if (word.includes(token) || word.startsWith(token)) return true;
  return token.length >= 3 && word.length >= 3 && editDistance(token, word) <= 1;
}

export function queryMatchesName(query, name) {
  const q = String(query || "").trim().toLowerCase();
  const n = String(name || "").trim().toLowerCase();
  if (!q || !n) return false;
  if (n.includes(q)) return true;
  const qParts = tokens(q);
  const nParts = tokens(n);
  return qParts.every((qp) => nParts.some((np) => tokenMatches(np, qp)));
}

export function scoreName(query, name) {
  const q = String(query || "").trim().toLowerCase();
  const n = String(name || "").trim().toLowerCase();
  if (q.length < 2 || !n) return 0;
  if (n === q) return 180;
  const qParts = tokens(q);
  const nParts = tokens(n);
  if (!qParts.length || !nParts.length) return 0;
  const prefixEvery = qParts.every((qp) => nParts.some((np) => np.startsWith(qp)));
  const fuzzyEvery = qParts.every((qp) => nParts.some((np) => tokenMatches(np, qp)));
  const contains = n.includes(q);
  if (!prefixEvery && !contains && !fuzzyEvery) return 0;
  let score = 0;
  if (n.startsWith(q)) score += 100;
  if (nParts[0].startsWith(qParts[0])) score += 40;
  if (prefixEvery) score += 30;
  else if (fuzzyEvery) score += 22;
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
    const nameScore = scoreName(q, name);
    const nickScore = nick ? scoreName(q, nick) : 0;
    const score = Math.max(nameScore, nickScore ? nickScore + 4 : 0);
    if (score > 0) scored.push({ person, score });
  }
  scored.sort((a, b) => b.score - a.score || String(a.person.name).localeCompare(String(b.person.name)));
  return scored.slice(0, limit).map((row) => row.person);
}

function firstTokens(person) {
  const first = tokens(person?.name)[0] || "";
  const nickFirst = tokens(String(person?.nickname || "").split(/[,;/]/)[0])[0] || "";
  return { first, nickFirst };
}

export function uniqueFirstName(query, people, { excludeId } = {}) {
  const q = String(query || "").trim().toLowerCase();
  if (q.length < 2) return null;
  const hits = [];
  for (const person of people || []) {
    if (person?.unknown_name) continue;
    if (excludeId != null && String(person.id) === String(excludeId)) continue;
    const { first, nickFirst } = firstTokens(person);
    if (first === q || nickFirst === q || first.startsWith(q) || nickFirst.startsWith(q)) hits.push(person);
  }
  return hits.length === 1 ? hits[0] : null;
}

function uniqueNamePrefix(query, people, { excludeId } = {}) {
  const q = String(query || "").trim().toLowerCase();
  if (q.length < 2) return null;
  const hits = [];
  for (const person of people || []) {
    if (person?.unknown_name) continue;
    if (excludeId != null && String(person.id) === String(excludeId)) continue;
    const name = String(person.name || "").trim().toLowerCase();
    const nick = String(person.nickname || "").trim().toLowerCase();
    if (!name) continue;
    if (name === q || name.startsWith(q) || (nick && (nick === q || nick.startsWith(q)))) hits.push(person);
  }
  return hits.length === 1 ? hits[0] : null;
}

/** Person uniquely identified by what was typed: unique first name, unique full-name prefix, or a single catalog hit. */
export function uniqueCatalogPerson(query, people, { excludeId } = {}) {
  const uniqueFirst = uniqueFirstName(query, people, { excludeId });
  if (uniqueFirst) return uniqueFirst;
  const uniquePrefix = uniqueNamePrefix(query, people, { excludeId });
  if (uniquePrefix) return uniquePrefix;
  const hits = matchPeople(query, people, { excludeId, limit: 2 });
  return hits.length === 1 ? hits[0] : null;
}

export function completeUniqueFirstName(query, people, { excludeId } = {}) {
  const unique = uniqueFirstName(query, people, { excludeId });
  if (!unique) return null;
  const q = String(query || "").trim().toLowerCase();
  const { first, nickFirst } = firstTokens(unique);
  if (first === q || nickFirst === q) return unique;
  return null;
}

export function splitNameMatch(query, name) {
  const raw = String(name || "");
  const q = String(query || "").trim();
  if (q && raw.toLowerCase().startsWith(q.toLowerCase())) {
    return { head: raw.slice(0, q.length), rest: raw.slice(q.length) };
  }
  return { head: "", rest: raw };
}
