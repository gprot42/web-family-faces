export const CARD_W = 176;
export const CARD_H = 64;
const GAP_X = 36;
const ROW = 136;
const PAD = 32;

function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

function midX(ids, pos) {
  const pts = ids.map((id) => pos[id]).filter(Boolean);
  if (!pts.length) return 0;
  return pts.reduce((sum, p) => sum + p.x, 0) / pts.length + CARD_W / 2;
}

function placeRow(ids, y, centerX, pos) {
  const unique = [];
  for (const id of ids) {
    if (id && !unique.includes(id)) unique.push(id);
  }
  if (!unique.length) return;
  const total = unique.length * CARD_W + Math.max(0, unique.length - 1) * GAP_X;
  let x = centerX - total / 2;
  unique.forEach((id) => {
    pos[id] = { x, y };
    x += CARD_W + GAP_X;
  });
}

function generationOf(union) {
  const g = Number(union?.generation);
  if (Number.isFinite(g) && g > 0) return g;
  if (union?.role === "parents") return 1;
  if (union?.role === "grandparents") return 2;
  if (union?.role === "ancestors") return 3;
  return 0;
}

function resolveRow(pos, y) {
  const row = Object.entries(pos)
    .filter(([, p]) => p.y === y)
    .sort((a, b) => a[1].x - b[1].x);
  for (let i = 1; i < row.length; i += 1) {
    const prev = row[i - 1][1];
    const cur = row[i][1];
    const minX = prev.x + CARD_W + GAP_X;
    if (cur.x < minX) cur.x = minX;
  }
}

function parentUnionOf(pid, ancestorUnions) {
  return ancestorUnions.find((u) => (u.children || []).includes(pid)) || null;
}

function ancestorIds(pid, ancestorUnions) {
  const out = [];
  const stack = [pid];
  const seen = new Set();
  while (stack.length) {
    const cur = stack.pop();
    const union = parentUnionOf(cur, ancestorUnions);
    if (!union) continue;
    for (const parentId of union.partners || []) {
      if (!parentId || seen.has(parentId)) continue;
      seen.add(parentId);
      out.push(parentId);
      stack.push(parentId);
    }
  }
  return out;
}

function resolveUnionRow(pos, rowUnions, y, ancestorUnions) {
  const blocks = [];
  for (const union of rowUnions) {
    const parts = (union.partners || []).filter((id) => pos[id] && pos[id].y === y);
    if (!parts.length) continue;
    const xs = parts.map((id) => pos[id].x);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    blocks.push({ ids: parts, x: minX, right: maxX + CARD_W });
  }
  blocks.sort((a, b) => a.x - b.x);
  for (let i = 1; i < blocks.length; i += 1) {
    const minX = blocks[i - 1].right + GAP_X;
    if (blocks[i].x >= minX) continue;
    const dx = minX - blocks[i].x;
    const shiftIds = new Set(blocks[i].ids);
    for (const id of blocks[i].ids) {
      for (const anc of ancestorIds(id, ancestorUnions)) shiftIds.add(anc);
    }
    for (const id of shiftIds) {
      if (pos[id]) pos[id].x += dx;
    }
    blocks[i].x += dx;
    blocks[i].right += dx;
  }
}

function shiftPositive(pos) {
  const xs = Object.values(pos).map((p) => p.x);
  const ys = Object.values(pos).map((p) => p.y);
  if (!xs.length) return { width: PAD * 2, height: PAD * 2 };
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  const dx = PAD - minX;
  const dy = PAD - minY;
  for (const p of Object.values(pos)) {
    p.x += dx;
    p.y += dy;
  }
  const maxX = Math.max(...Object.values(pos).map((p) => p.x));
  const maxY = Math.max(...Object.values(pos).map((p) => p.y));
  return { width: maxX + CARD_W + PAD, height: maxY + CARD_H + PAD };
}

export function layoutFamilyTree(chart) {
  const focusId = chart?.focus;
  const nodeMap = Object.fromEntries((chart?.nodes || []).map((n) => [n.id, n]));
  const unions = chart?.unions || [];
  const pos = {};
  if (!focusId) {
    return { nodes: [], edges: [], width: 400, height: 240, focus: focusId };
  }
  if (!nodeMap[focusId]) {
    nodeMap[focusId] = { id: focusId, name: focusId };
  }

  const own = unions.filter((u) => u.role === "own" && (u.partners || []).includes(focusId));
  const ancestorU = unions.filter((u) => generationOf(u) > 0);
  const gcU = unions.filter((u) => u.role === "grandchildren");
  const maxAncGen = ancestorU.reduce((m, u) => Math.max(m, generationOf(u)), 0);

  const yOfGen = (g) => (maxAncGen - g) * ROW;
  const yFocus = maxAncGen * ROW;
  const yChild = yFocus + ROW;
  const yGC = yChild + ROW;

  pos[focusId] = { x: 0, y: yFocus };

  let spouseX = CARD_W + GAP_X;
  for (const union of own) {
    const spouse = (union.partners || []).find((id) => id && id !== focusId && nodeMap[id]);
    if (spouse && !pos[spouse]) {
      pos[spouse] = { x: spouseX, y: yFocus };
      spouseX += CARD_W + GAP_X;
    }
  }

  let childMin = -Infinity;
  for (const union of own) {
    const kids = (union.children || []).filter((id) => nodeMap[id] && id !== focusId);
    if (!kids.length) continue;
    const partners = (union.partners || []).filter((id) => pos[id]);
    const mid = partners.length ? midX(partners, pos) : pos[focusId].x + CARD_W / 2;
    const total = kids.length * CARD_W + Math.max(0, kids.length - 1) * GAP_X;
    let start = mid - total / 2;
    if (start < childMin) start = childMin;
    kids.forEach((id, i) => {
      pos[id] = { x: start + i * (CARD_W + GAP_X), y: yChild };
    });
    childMin = start + total + GAP_X;
  }

  for (let g = 1; g <= maxAncGen; g += 1) {
    const y = yOfGen(g);
    const rowUnions = ancestorU.filter((u) => generationOf(u) === g);
    rowUnions.sort((a, b) => {
      const childA = (a.children || []).find((id) => pos[id]);
      const childB = (b.children || []).find((id) => pos[id]);
      return (childA ? pos[childA].x : 0) - (childB ? pos[childB].x : 0);
    });
    for (const union of rowUnions) {
      const kids = (union.children || []).filter((id) => pos[id]);
      if (!kids.length) continue;
      const parts = (union.partners || []).filter((id) => nodeMap[id]);
      const unplaced = parts.filter((id) => !pos[id]);
      if (!unplaced.length) continue;
      const placed = parts.filter((id) => pos[id]);
      if (placed.length) {
        const start = Math.max(...placed.map((id) => pos[id].x)) + CARD_W + GAP_X;
        unplaced.forEach((id, i) => {
          pos[id] = { x: start + i * (CARD_W + GAP_X), y };
        });
      } else {
        placeRow(unplaced, y, midX(kids, pos), pos);
      }
    }
  }
  for (let g = 1; g <= maxAncGen; g += 1) {
    const rowUnions = ancestorU.filter((u) => generationOf(u) === g);
    resolveUnionRow(pos, rowUnions, yOfGen(g), ancestorU);
  }

  let gcMin = -Infinity;
  for (const union of gcU) {
    const parents = (union.partners || []).filter((id) => pos[id] && pos[id].y === yChild);
    const kids = (union.children || []).filter((id) => nodeMap[id] && !pos[id]);
    if (!parents.length || !kids.length) continue;
    const mid = midX(parents, pos);
    const total = kids.length * CARD_W + Math.max(0, kids.length - 1) * GAP_X;
    let start = mid - total / 2;
    if (start < gcMin) start = gcMin;
    kids.forEach((id, i) => {
      pos[id] = { x: start + i * (CARD_W + GAP_X), y: yGC };
    });
    gcMin = start + total + GAP_X;
  }
  resolveRow(pos, yChild);
  resolveRow(pos, yGC);
  resolveRow(pos, yFocus);

  const size = shiftPositive(pos);
  const edges = [];

  function marriageEdge(union) {
    const parts = (union.partners || []).map((id) => pos[id]).filter(Boolean);
    if (parts.length < 2) return null;
    parts.sort((a, b) => a.x - b.x);
    const y = parts[0].y + CARD_H / 2;
    return {
      type: "marriage",
      x1: parts[0].x + CARD_W,
      y1: y,
      x2: parts[1].x,
      y2: y,
      label: union.marriage?.year ? String(union.marriage.year) : "",
    };
  }

  function descentEdges(union) {
    const partners = (union.partners || []).filter((id) => pos[id]);
    if (!partners.length) return [];
    const parentY = Math.min(...partners.map((id) => pos[id].y));
    const kids = (union.children || []).filter((id) => pos[id] && pos[id].y > parentY + 8);
    if (!kids.length) return [];
    const mx = midX(partners, pos);
    const joinY = parentY + CARD_H;
    const barY = Math.min(...kids.map((id) => pos[id].y)) - 26;
    const kidPts = kids
      .map((id) => ({ x: pos[id].x + CARD_W / 2, y: pos[id].y }))
      .sort((a, b) => a.x - b.x);
    const left = Math.min(mx, kidPts[0].x);
    const right = Math.max(mx, kidPts[kidPts.length - 1].x);
    const lines = [{ type: "stem", x1: mx, y1: joinY, x2: mx, y2: barY }];
    if (right - left > 1) {
      lines.push({ type: "stem", x1: left, y1: barY, x2: right, y2: barY });
    }
    for (const pt of kidPts) {
      lines.push({ type: "arrow", x1: pt.x, y1: barY, x2: pt.x, y2: pt.y - 6 });
    }
    return lines;
  }

  for (const union of unions) {
    const placedPartners = (union.partners || []).filter((id) => pos[id]);
    if (placedPartners.length >= 2) {
      const m = marriageEdge(union);
      if (m) edges.push(m);
    }
    edges.push(...descentEdges(union));
  }

  const nodes = Object.entries(pos).map(([id, p]) => ({
    ...(nodeMap[id] || { id, name: id }),
    x: p.x,
    y: p.y,
    focus: id === focusId,
  }));

  return { nodes, edges, width: size.width, height: size.height, focus: focusId };
}

export function clampZoom(value) {
  return clamp(Number(value) || 1, 0.12, 2.5);
}

export function viewOnFocus(layout, viewW, viewH) {
  const nodes = layout?.nodes || [];
  const focus = nodes.find((n) => n.focus) || nodes[0];
  const width = Number(viewW) || 0;
  const height = Number(viewH) || 0;
  if (!focus || width < 80 || height < 80) {
    return { x: 0, y: 0, zoom: 1 };
  }
  const pad = 48;
  const near = nodes.filter((n) => Math.abs((n.y || 0) - focus.y) <= ROW + CARD_H);
  const xs = [focus.x, ...near.map((n) => n.x)];
  const ys = [focus.y, ...near.map((n) => n.y)];
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs.map((x) => x + CARD_W)) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys.map((y) => y + CARD_H)) + pad;
  const boxW = Math.max(CARD_W + pad * 2, maxX - minX);
  const boxH = Math.max(CARD_H + pad * 2, maxY - minY);
  const zoom = clampZoom(Math.min((width - 24) / boxW, (height - 24) / boxH, 1.15));
  return {
    x: width / 2 - (focus.x + CARD_W / 2) * zoom,
    y: height * 0.62 - (focus.y + CARD_H / 2) * zoom,
    zoom,
  };
}

export function wheelZoomFactor(event) {
  let dy = event.deltaY;
  if (event.deltaMode === 1) dy *= 16;
  if (event.deltaMode === 2) dy *= 800;
  const k = event.ctrlKey ? 0.01 : 0.0024;
  return Math.exp(-dy * k);
}

export function treePersonIdForCatalog(people, catalogId, catalogName = "") {
  const id = Number(catalogId);
  if (!id) return "";
  const linked = (people || []).find((person) => Number(person.catalog_id) === id);
  if (linked?.id) return linked.id;
  const needle = String(catalogName || "").trim().toLowerCase();
  if (!needle) return "";
  const named = (people || []).filter((person) => String(person.name || "").trim().toLowerCase() === needle);
  return named.length === 1 ? named[0].id : "";
}
