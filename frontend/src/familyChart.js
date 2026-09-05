export const CARD_W = 200;
export const CARD_H = 156;
// The portrait circle sits above the card top; lines stop short of it.
export const PHOTO_OVERHANG = 30;
const GAP_X = 36;
export const ROW = CARD_H + 104;
const PAD = 48;

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

function layoutEntireTree(chart) {
  const focusId = chart?.focus;
  const nodeMap = Object.fromEntries((chart?.nodes || []).map((n) => [n.id, n]));
  const unions = chart?.unions || [];
  const pos = {};
  if (!focusId) {
    return { nodes: [], edges: [], width: 400, height: 240, focus: focusId };
  }
  if (!nodeMap[focusId]) nodeMap[focusId] = { id: focusId, name: focusId, generation: 0 };

  const spouseGap = 16;
  pos[focusId] = { x: 0, y: 0 };

  function placeId(id, x, y) {
    if (!id || !nodeMap[id] || pos[id]) return;
    pos[id] = { x, y };
  }

  function membersOf(union) {
    const parts = [...new Set((union.partners || []).filter((id) => nodeMap[id]))];
    const kids = [...new Set((union.children || []).filter((id) => nodeMap[id]))];
    return { parts, kids };
  }

  for (let step = 0; step < unions.length + 8; step += 1) {
    let progressed = false;
    const ranked = [...unions].sort((a, b) => {
      const sa = (a.partners || []).filter((id) => pos[id]).length * 10 + (a.children || []).filter((id) => pos[id]).length;
      const sb = (b.partners || []).filter((id) => pos[id]).length * 10 + (b.children || []).filter((id) => pos[id]).length;
      return sb - sa;
    });
    for (const union of ranked) {
      const { parts, kids } = membersOf(union);
      const placedParts = parts.filter((id) => pos[id]);
      const placedKids = kids.filter((id) => pos[id]);
      if (!placedParts.length && !placedKids.length) continue;

      if (placedParts.length && placedParts.length < parts.length) {
        const y = pos[placedParts[0]].y;
        let x = Math.max(...placedParts.map((id) => pos[id].x)) + CARD_W + spouseGap;
        for (const id of parts) {
          if (pos[id]) continue;
          placeId(id, x, y);
          x += CARD_W + spouseGap;
          progressed = true;
        }
      }
      if (placedKids.length && parts.some((id) => !pos[id])) {
        const y = Math.min(...placedKids.map((id) => pos[id].y)) - ROW;
        const missing = parts.filter((id) => !pos[id]);
        const total = missing.length * CARD_W + Math.max(0, missing.length - 1) * spouseGap;
        let x = midX(placedKids, pos) - total / 2;
        missing.forEach((id, i) => {
          placeId(id, x + i * (CARD_W + spouseGap), y);
          progressed = true;
        });
      }
      const nowParts = parts.filter((id) => pos[id]);
      const missingKids = kids.filter((id) => !pos[id]);
      if (nowParts.length && missingKids.length) {
        const y = Math.min(...nowParts.map((id) => pos[id].y)) + ROW;
        const total = missingKids.length * CARD_W + Math.max(0, missingKids.length - 1) * GAP_X;
        let x = midX(nowParts, pos) - total / 2;
        missingKids.forEach((id, i) => {
          placeId(id, x + i * (CARD_W + GAP_X), y);
          progressed = true;
        });
      }
    }
    if (progressed) continue;
    const nextId = (chart.nodes || [])
      .map((n) => n.id)
      .concat(unions.flatMap((u) => [...(u.partners || []), ...(u.children || [])]))
      .find((id) => id && nodeMap[id] && !pos[id]);
    if (!nextId) break;
    const xs = Object.values(pos).map((p) => p.x);
    placeId(nextId, (xs.length ? Math.max(...xs) : 0) + CARD_W + GAP_X * 4, 0);
  }

  for (const node of chart.nodes || []) {
    if (pos[node.id]) continue;
    const xs = Object.values(pos).map((p) => p.x);
    pos[node.id] = { x: (xs.length ? Math.max(...xs) : 0) + CARD_W + GAP_X, y: 0 };
  }

  function sameY(a, b) {
    return Math.abs(a - b) <= 8;
  }

  function spanOf(ids) {
    const pts = ids.map((id) => pos[id]).filter(Boolean);
    if (!pts.length) return { x: 0, right: 0, mid: 0, width: 0 };
    const x = Math.min(...pts.map((p) => p.x));
    const right = Math.max(...pts.map((p) => p.x + CARD_W));
    return { x, right, mid: (x + right) / 2, width: right - x };
  }

  function descendantsOf(seed) {
    const out = new Set(seed.filter((id) => id && pos[id]));
    const stack = [...out];
    while (stack.length) {
      const id = stack.pop();
      for (const union of unions) {
        const parts = union.partners || [];
        if (!parts.includes(id)) continue;
        for (const p of parts) {
          if (!p || !pos[p] || out.has(p)) continue;
          out.add(p);
          stack.push(p);
        }
        for (const k of union.children || []) {
          if (!k || !pos[k] || out.has(k)) continue;
          out.add(k);
          stack.push(k);
        }
      }
    }
    return out;
  }

  function unitForPerson(id, y) {
    const spouses = [];
    for (const union of unions) {
      const parts = (union.partners || []).filter((p) => p && nodeMap[p]);
      if (!parts.includes(id)) continue;
      for (const p of parts) {
        if (p === id) continue;
        if (!pos[p]) placeId(p, (pos[id]?.x || 0) + CARD_W + spouseGap, y);
        else if (sameY(pos[p].y, y) || Math.abs(pos[p].y - y) <= ROW / 2) pos[p].y = y;
        if (pos[p] && sameY(pos[p].y, y) && !spouses.includes(p)) spouses.push(p);
      }
    }
    spouses.sort((a, b) => pos[a].x - pos[b].x || String(a).localeCompare(String(b)));
    if (!spouses.length) return [id];
    if (spouses.length === 1) {
      return pos[spouses[0]].x < pos[id].x ? [spouses[0], id] : [id, spouses[0]];
    }
    const left = spouses.filter((s) => pos[s].x < pos[id].x);
    const right = spouses.filter((s) => pos[s].x >= pos[id].x);
    if (!left.length) return [spouses[0], id, ...spouses.slice(1)];
    if (!right.length) return [...spouses.slice(0, -1), id, spouses[spouses.length - 1]];
    return [...left, id, ...right];
  }

  function layoutUnit(unit, y, startX) {
    let x = startX;
    unit.forEach((id, i) => {
      if (!pos[id]) placeId(id, x, y);
      if (i) x += spouseGap;
      pos[id].y = y;
      pos[id].x = x;
      x += CARD_W;
    });
    return x;
  }

  function packUnionKids(union) {
    const { parts, kids } = membersOf(union);
    const placedParts = parts.filter((id) => pos[id]);
    if (!placedParts.length || !kids.length) return;
    const parentY = Math.min(...placedParts.map((id) => pos[id].y));
    const childY = parentY + ROW;
    const childList = kids.filter((id) => nodeMap[id]);
    if (!childList.length) return;
    for (const id of childList) {
      if (!pos[id]) placeId(id, 0, childY);
      pos[id].y = childY;
    }
    const slots = childList.map((id) => {
      const unit = unitForPerson(id, childY);
      for (const p of unit) {
        if (!pos[p]) placeId(p, 0, childY);
        pos[p].y = childY;
      }
      const own = unit.length * CARD_W + Math.max(0, unit.length - 1) * spouseGap;
      const below = [...descendantsOf(unit)].filter((pid) => pos[pid] && pos[pid].y > childY + 8);
      const belowW = below.length ? spanOf(below).width : 0;
      return { unit, own, width: Math.max(own, belowW) };
    });
    const total = slots.reduce((sum, slot, i) => sum + slot.width + (i ? GAP_X : 0), 0);
    let x = midX(placedParts, pos) - total / 2;
    for (const slot of slots) {
      const treeIds = [...descendantsOf(slot.unit)];
      layoutUnit(slot.unit, childY, x + (slot.width - slot.own) / 2);
      const below = treeIds.filter((id) => pos[id] && !slot.unit.includes(id));
      if (below.length) {
        const dx = spanOf(slot.unit).mid - spanOf(below).mid;
        if (Math.abs(dx) >= 1) {
          for (const id of below) pos[id].x += dx;
        }
      }
      x += slot.width + GAP_X;
    }
  }

  function blocksOnRow(y) {
    const used = new Set();
    const blocks = [];
    function addBlock(ids) {
      const unique = ids.filter((id) => pos[id] && sameY(pos[id].y, y) && !used.has(id));
      if (!unique.length) return;
      unique.forEach((id) => used.add(id));
      const sp = spanOf(unique);
      blocks.push({ ids: unique, x: sp.x, right: sp.right });
    }
    for (const union of unions) {
      const { kids } = membersOf(union);
      const onRow = [];
      for (const kid of kids) {
        if (!pos[kid] || !sameY(pos[kid].y, y)) continue;
        for (const id of unitForPerson(kid, y)) {
          if (!onRow.includes(id)) onRow.push(id);
        }
      }
      addBlock(onRow);
    }
    for (const id of Object.keys(pos)) {
      if (!sameY(pos[id].y, y) || used.has(id)) continue;
      addBlock(unitForPerson(id, y));
    }
    return blocks;
  }

  function packRowSubtrees(y) {
    const blocks = blocksOnRow(y);
    blocks.sort((a, b) => a.x - b.x || String(a.ids[0]).localeCompare(String(b.ids[0])));
    for (let i = 1; i < blocks.length; i += 1) {
      const minX = blocks[i - 1].right + GAP_X;
      if (blocks[i].x >= minX) continue;
      const dx = minX - blocks[i].x;
      const earlier = new Set(blocks.slice(0, i).flatMap((block) => [...descendantsOf(block.ids)]));
      for (const id of descendantsOf(blocks[i].ids)) {
        if (earlier.has(id) || !pos[id]) continue;
        pos[id].x += dx;
      }
      const sp = spanOf(blocks[i].ids);
      blocks[i].x = sp.x;
      blocks[i].right = sp.right;
    }
  }

  for (const union of unions) {
    const { parts } = membersOf(union);
    const placed = parts.filter((id) => pos[id]);
    if (placed.length < 2) continue;
    const y = pos[placed[0]].y;
    for (const id of placed) {
      if (sameY(pos[id].y, y)) pos[id].y = y;
    }
  }

  const parentYOf = (union) => {
    const parts = membersOf(union).parts.filter((id) => pos[id]);
    return parts.length ? Math.min(...parts.map((id) => pos[id].y)) : Infinity;
  };
  [...unions]
    .sort((a, b) => parentYOf(b) - parentYOf(a) || String(a.id).localeCompare(String(b.id)))
    .forEach(packUnionKids);

  const rows = [...new Set(Object.values(pos).map((p) => p.y))].sort((a, b) => a - b);
  for (const y of rows) packRowSubtrees(y);
  for (const y of rows) resolveRow(pos, y);
  squeezeEmptyBands(pos);

  const size = shiftPositive(pos);
  const edges = [];

  function marriageEdge(union) {
    const parts = (union.partners || []).map((id) => pos[id]).filter(Boolean);
    if (parts.length < 2) return null;
    parts.sort((a, b) => a.x - b.x);
    if (Math.abs(parts[0].y - parts[1].y) > 8) return null;
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
    const kids = (union.children || []).filter(
      (id) => pos[id] && Math.abs(pos[id].y - (parentY + ROW)) < ROW / 2,
    );
    if (!kids.length) return [];
    const mx = midX(partners, pos);
    const joinY = parentY + CARD_H;
    const childY = Math.min(...kids.map((id) => pos[id].y));
    const barY = childY - PHOTO_OVERHANG - 30;
    const kidPts = kids
      .map((id) => ({ x: pos[id].x + CARD_W / 2, y: pos[id].y, id }))
      .sort((a, b) => a.x - b.x);
    const ownFamily = new Set(kids);
    for (const kid of kids) {
      for (const id of unitForPerson(kid, childY)) ownFamily.add(id);
    }
    const splitters = Object.keys(pos).filter((id) => {
      if (ownFamily.has(id) || !pos[id] || Math.abs(pos[id].y - childY) > 8) return false;
      return true;
    });
    function blocked(x1, x2) {
      const lo = Math.min(x1, x2);
      const hi = Math.max(x1, x2);
      return splitters.some((id) => {
        const cx = pos[id].x + CARD_W / 2;
        return cx > lo + 4 && cx < hi - 4;
      });
    }
    const clusters = [];
    for (const pt of kidPts) {
      const last = clusters[clusters.length - 1];
      if (!last || blocked(last[last.length - 1].x, pt.x)) clusters.push([pt]);
      else last.push(pt);
    }
    const lines = [{ type: "stem", x1: mx, y1: joinY, x2: mx, y2: barY }];
    for (const cluster of clusters) {
      const left = cluster[0].x;
      const right = cluster[cluster.length - 1].x;
      let barLo = left;
      let barHi = right;
      if (clusters.length === 1 && !blocked(mx, left) && !blocked(mx, right)) {
        barLo = Math.min(left, mx);
        barHi = Math.max(right, mx);
      }
      if (barHi - barLo > 1) lines.push({ type: "stem", x1: barLo, y1: barY, x2: barHi, y2: barY });
      for (const pt of cluster) {
        lines.push({ type: "arrow", x1: pt.x, y1: barY, x2: pt.x, y2: pt.y - PHOTO_OVERHANG - 4 });
      }
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

export function layoutFamilyTree(chart) {
  if (chart?.scope === "all") return layoutEntireTree(chart);
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

  // Brothers and sisters share the focus row: older ones to the left, younger
  // ones after the spouse, in the order the file lists them.
  let leftX = pos[focusId].x;
  for (const union of ancestorU) {
    if (generationOf(union) !== 1) continue;
    const kids = (union.children || []).filter((id) => nodeMap[id]);
    const at = kids.indexOf(focusId);
    if (at < 0) continue;
    for (const id of kids.slice(0, at).reverse()) {
      if (pos[id]) continue;
      leftX -= CARD_W + GAP_X;
      pos[id] = { x: leftX, y: yFocus };
    }
    for (const id of kids.slice(at + 1)) {
      if (pos[id]) continue;
      pos[id] = { x: spouseX, y: yFocus };
      spouseX += CARD_W + GAP_X;
    }
  }

  // Each child of the focus gets a slot as wide as their own children below,
  // so a grandchild never lands under an aunt or uncle.
  function kidsOfChild(id) {
    const out = [];
    for (const union of gcU) {
      if (!(union.partners || []).includes(id)) continue;
      for (const k of union.children || []) {
        if (nodeMap[k] && k !== focusId && !out.includes(k)) out.push(k);
      }
    }
    return out;
  }
  const slotOf = {};
  let childMin = -Infinity;
  for (const union of own) {
    const kids = (union.children || []).filter((id) => nodeMap[id] && id !== focusId);
    if (!kids.length) continue;
    const partners = (union.partners || []).filter((id) => pos[id]);
    const mid = partners.length ? midX(partners, pos) : pos[focusId].x + CARD_W / 2;
    const widths = kids.map((id) => {
      const n = kidsOfChild(id).length;
      return Math.max(CARD_W, n * CARD_W + Math.max(0, n - 1) * GAP_X);
    });
    const total = widths.reduce((sum, w) => sum + w, 0) + Math.max(0, kids.length - 1) * GAP_X;
    let start = mid - total / 2;
    if (start < childMin) start = childMin;
    let x = start;
    kids.forEach((id, i) => {
      pos[id] = { x: x + (widths[i] - CARD_W) / 2, y: yChild };
      slotOf[id] = { x, w: widths[i] };
      x += widths[i] + GAP_X;
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
    const total = kids.length * CARD_W + Math.max(0, kids.length - 1) * GAP_X;
    const slot = slotOf[parents[0]];
    let start = slot ? slot.x + (slot.w - total) / 2 : midX(parents, pos) - total / 2;
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
    const barY = Math.min(...kids.map((id) => pos[id].y)) - PHOTO_OVERHANG - 30;
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
      lines.push({ type: "arrow", x1: pt.x, y1: barY, x2: pt.x, y2: pt.y - PHOTO_OVERHANG - 4 });
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

export function clampZoom(value, min = 0.12) {
  const lo = clamp(Number(min) || 0.12, 0.005, 0.12);
  return clamp(Number(value) || 1, lo, 2.5);
}

/** Close empty vertical bands that no card in any row occupies, keeping order. */
export function squeezeEmptyBands(pos, maxGap = GAP_X * 3) {
  const spans = Object.values(pos)
    .map((p) => [p.x, p.x + CARD_W])
    .sort((a, b) => a[0] - b[0]);
  if (!spans.length) return;
  const bands = [];
  for (const [x0, x1] of spans) {
    const last = bands[bands.length - 1];
    if (last && x0 <= last[1] + maxGap) last[1] = Math.max(last[1], x1);
    else bands.push([x0, x1]);
  }
  if (bands.length < 2) return;
  // For each band after the first: shift = accumulated width removed to its left.
  const shifts = [];
  let removed = 0;
  for (let i = 1; i < bands.length; i += 1) {
    const gap = bands[i][0] - bands[i - 1][1];
    removed += Math.max(0, gap - maxGap);
    shifts.push({ from: bands[i][0], dx: removed });
  }
  for (const p of Object.values(pos)) {
    let dx = 0;
    for (const s of shifts) {
      if (p.x >= s.from - 0.5) dx = s.dx;
      else break;
    }
    p.x -= dx;
  }
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

// ---------------------------------------------------------------------------
// Double ancestor chart: the person in the middle with brothers and sisters,
// the father's line fanning out to the right and the mother's to the left,
// one column per generation. Cards are compact and horizontal.
export const DCARD_W = 196;
export const DCARD_H = 72;
const DCOL = DCARD_W + 64;
const DROW = DCARD_H + 14;
export const DOUBLE_DEPTH = 5;

export function layoutDoubleAncestorChart(chart, { depth = DOUBLE_DEPTH } = {}) {
  const focusId = chart?.focus;
  const nodeMap = Object.fromEntries((chart?.nodes || []).map((n) => [n.id, n]));
  const unions = chart?.unions || [];
  if (!focusId) return { nodes: [], edges: [], width: 400, height: 240, focus: focusId, mode: "double" };
  if (!nodeMap[focusId]) nodeMap[focusId] = { id: focusId, name: focusId };

  function parentsOf(id) {
    const union = unions.find((u) => (u.children || []).includes(id) && (u.partners || []).some((p) => nodeMap[p]));
    if (!union) return { father: null, mother: null, union: null };
    const parts = (union.partners || []).filter((p) => nodeMap[p]);
    let father = parts.find((p) => nodeMap[p].sex === "M");
    let mother = parts.find((p) => p !== father && nodeMap[p].sex === "F");
    if (!father && !mother) [father, mother] = parts;
    else if (!father) father = parts.find((p) => p !== mother) || null;
    else if (!mother) mother = parts.find((p) => p !== father) || null;
    return { father: father || null, mother: mother || null, union };
  }

  const placed = {};
  const links = [];

  // Lay one side out in local coordinates: column d (1 = parent), rows from a
  // cursor so every leaf gets its own row and each person sits between their parents.
  function buildSide(rootId, dir, sideBranch) {
    let cursor = 0;
    const lastInCol = {};
    function walk(id, d, branch) {
      if (!id || placed[id]) return null;
      const own = d === 1 ? sideBranch : branch;
      let y;
      let parents = { father: null, mother: null };
      if (d < depth) parents = parentsOf(id);
      const ups = [];
      if (parents.father && !placed[parents.father]) {
        ups.push(walk(parents.father, d + 1, d === 1 ? `${sideBranch}f` : own));
      }
      if (parents.mother && !placed[parents.mother]) {
        ups.push(walk(parents.mother, d + 1, d === 1 ? `${sideBranch}m` : own));
      }
      const upYs = ups.filter((v) => v != null);
      if (upYs.length) y = (Math.min(...upYs) + Math.max(...upYs)) / 2;
      else {
        y = cursor;
        cursor += DROW;
      }
      if (lastInCol[d] != null && y < lastInCol[d] + DROW) y = lastInCol[d] + DROW;
      lastInCol[d] = y;
      placed[id] = { x: dir * d * DCOL, y, d, dir, branch: own };
      for (const pid of [parents.father, parents.mother]) {
        if (pid && placed[pid]) links.push({ child: id, parent: pid, dir });
      }
      return y;
    }
    return walk(rootId, 1, sideBranch);
  }

  const own = parentsOf(focusId);
  // Right side first so the father's line is laid out before the mother's.
  const yFather = own.father ? buildSide(own.father, 1, "f") : null;
  const yMother = own.mother ? buildSide(own.mother, -1, "m") : null;
  const sides = { 1: yFather, [-1]: yMother };
  const focusY = Math.max(yFather ?? 0, yMother ?? 0);
  for (const p of Object.values(placed)) {
    const rootY = sides[p.dir];
    if (rootY != null) p.y += focusY - rootY;
  }
  placed[focusId] = { x: 0, y: focusY, d: 0, dir: 0, branch: "self" };
  // The parents sit either side of their child; say they are a couple.
  const marriage = own.union?.marriage?.year ? `married ${own.union.marriage.year}` : own.father && own.mother ? "married" : "";
  if (own.father) links.push({ child: focusId, parent: own.father, dir: 1, label: marriage });
  if (own.mother) links.push({ child: focusId, parent: own.mother, dir: -1 });

  // Brothers and sisters share the middle column, older above, younger below.
  const kids = ((own.union || {}).children || []).filter((id) => nodeMap[id]);
  const at = kids.indexOf(focusId);
  if (at >= 0) {
    kids.slice(0, at).reverse().forEach((id, i) => {
      if (!placed[id]) placed[id] = { x: 0, y: focusY - DROW * (i + 1), d: 0, dir: 0, branch: "sibling" };
    });
    kids.slice(at + 1).forEach((id, i) => {
      if (!placed[id]) placed[id] = { x: 0, y: focusY + DROW * (i + 1), d: 0, dir: 0, branch: "sibling" };
    });
  }

  const xs = Object.values(placed).map((p) => p.x);
  const ys = Object.values(placed).map((p) => p.y);
  const dx = PAD - Math.min(...xs);
  const dy = PAD + DCARD_H / 2 - Math.min(...ys);
  for (const p of Object.values(placed)) {
    p.x += dx;
    p.y += dy;
  }

  const edges = [];
  for (const link of links) {
    const c = placed[link.child];
    const p = placed[link.parent];
    if (!c || !p) continue;
    const cy = c.y + DCARD_H / 2;
    const py = p.y + DCARD_H / 2;
    const cx = link.dir > 0 ? c.x + DCARD_W : c.x;
    const px = link.dir > 0 ? p.x : p.x + DCARD_W;
    const bus = (cx + px) / 2;
    if (link.label && Math.abs(py - cy) <= 0.5) {
      edges.push({ type: "marriage", x1: cx, y1: cy, x2: px, y2: py, label: link.label });
      continue;
    }
    edges.push({ type: "stem", x1: cx, y1: cy, x2: bus, y2: cy });
    if (Math.abs(py - cy) > 0.5) edges.push({ type: "stem", x1: bus, y1: cy, x2: bus, y2: py });
    edges.push({ type: "stem", x1: bus, y1: py, x2: px, y2: py });
  }

  const nodes = Object.entries(placed).map(([id, p]) => ({
    ...(nodeMap[id] || { id, name: id }),
    x: p.x,
    y: p.y,
    w: DCARD_W,
    h: DCARD_H,
    focus: id === focusId,
    branch: p.branch,
    compact: true,
  }));
  const width = Math.max(...nodes.map((n) => n.x)) + DCARD_W + PAD;
  const height = Math.max(...nodes.map((n) => n.y)) + DCARD_H + PAD;
  return { nodes, edges, width, height, focus: focusId, mode: "double" };
}

// ---------------------------------------------------------------------------
// Fan chart: the person in a half disc at the bottom, each generation a ring,
// the father's line on the left half and the mother's on the right. Segments
// are SVG paths; colours follow the angle so each lineage keeps its hue.
export const FAN_DEPTH = 7;
export const FAN_MAX_DEPTH = 10;
const FAN_R0 = 110;
const FAN_RINGS = [150, 150, 140, 125, 105, 85, 70, 62, 56, 50];

function makeParentsOf(nodeMap, unions) {
  return function parentsOf(id) {
    const union = unions.find((u) => (u.children || []).includes(id) && (u.partners || []).some((p) => nodeMap[p]));
    if (!union) return { father: null, mother: null, union: null };
    const parts = (union.partners || []).filter((p) => nodeMap[p]);
    let father = parts.find((p) => nodeMap[p].sex === "M");
    let mother = parts.find((p) => p !== father && nodeMap[p].sex === "F");
    if (!father && !mother) [father, mother] = parts;
    else if (!father) father = parts.find((p) => p !== mother) || null;
    else if (!mother) mother = parts.find((p) => p !== father) || null;
    return { father: father || null, mother: mother || null, union };
  };
}

function polar(cx, cy, r, theta) {
  return { x: cx + r * Math.cos(theta), y: cy - r * Math.sin(theta) };
}

function ringPath(cx, cy, r0, r1, a0, a1) {
  // a0 < a1 in radians; the outer arc runs from a1 (left) to a0 (right).
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p1 = polar(cx, cy, r0, a1);
  const p2 = polar(cx, cy, r1, a1);
  const p3 = polar(cx, cy, r1, a0);
  const p4 = polar(cx, cy, r0, a0);
  return [
    `M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`,
    `L ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`,
    `A ${r1} ${r1} 0 ${large} 1 ${p3.x.toFixed(2)} ${p3.y.toFixed(2)}`,
    `L ${p4.x.toFixed(2)} ${p4.y.toFixed(2)}`,
    `A ${r0} ${r0} 0 ${large} 0 ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`,
    "Z",
  ].join(" ");
}

function arcPath(cx, cy, r, a0, a1) {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p1 = polar(cx, cy, r, a1);
  const p2 = polar(cx, cy, r, a0);
  return `M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
}

// Earthy palette in the app's own colours, not the pastel rainbow other
// tree apps use: terracotta to ochre across the father's half, sage to teal
// across the mother's, paler with each generation.
export function fanHue(theta) {
  const half = Math.PI / 2;
  if (theta >= half) return Math.round(38 + ((theta - half) / half) * (16 - 38));
  return Math.round(175 + (theta / half) * (95 - 175));
}

export function fanColours(theta, g, palette = null) {
  const hue = Math.round(palette?.hue ? palette.hue(theta) : fanHue(theta));
  const sat = palette?.sat ?? 46;
  const depth = Math.min(Math.max(g, 1), 9);
  const light = 78 + depth * 2;
  return {
    fill: `hsl(${hue} ${sat}% ${light}%)`,
    color: `hsl(${hue} ${Math.min(70, sat + 6)}% ${Math.max(38, 58 - depth * 2)}%)`,
  };
}

export function layoutFanChart(chart, { depth = FAN_DEPTH, palette = null } = {}) {
  const focusId = chart?.focus;
  const nodeMap = Object.fromEntries((chart?.nodes || []).map((n) => [n.id, n]));
  const unions = chart?.unions || [];
  if (!focusId) return { nodes: [], edges: [], width: 400, height: 240, focus: focusId, mode: "fan" };
  if (!nodeMap[focusId]) nodeMap[focusId] = { id: focusId, name: focusId };
  const parentsOf = makeParentsOf(nodeMap, unions);

  const rings = FAN_RINGS.slice(0, Math.max(1, Math.min(depth, FAN_RINGS.length)));
  const radii = [FAN_R0];
  for (const w of rings) radii.push(radii[radii.length - 1] + w);
  const rMax = radii[radii.length - 1];
  const cx = rMax + PAD;
  const cy = rMax + PAD;

  const nodes = [];
  const seen = new Set([focusId]);

  function place(id, g, a0, a1) {
    if (!id || !nodeMap[id] || g > rings.length || seen.has(id)) return;
    seen.add(id);
    const r0 = radii[g - 1];
    const r1 = radii[g];
    const mid = (a0 + a1) / 2;
    const span = a1 - a0;
    const arcAtInner = r0 * span;
    const radial = arcAtInner < 150 || g >= 4;
    const size = [15, 15, 15, 13, 11.5, 10, 8.5, 7.5, 7, 6.5, 6][Math.min(g, 10)];
    nodes.push({
      ...nodeMap[id],
      id,
      g,
      a0,
      a1,
      r0,
      r1,
      mid,
      cx,
      cy,
      path: ringPath(cx, cy, r0, r1, a0, a1),
      band: arcPath(cx, cy, r0 + 3, a0, a1),
      ...fanColours(mid, g, palette),
      radial,
      left: mid > Math.PI / 2,
      size,
      focus: false,
      fan: true,
    });
    const { father, mother } = parentsOf(id);
    // Father takes the left (larger angle) half of the span, mother the right.
    place(father, g + 1, mid, a1);
    place(mother, g + 1, a0, mid);
  }

  const own = parentsOf(focusId);
  place(own.father, 1, Math.PI / 2, Math.PI);
  place(own.mother, 1, 0, Math.PI / 2);

  nodes.unshift({
    ...nodeMap[focusId],
    id: focusId,
    g: 0,
    a0: 0,
    a1: Math.PI,
    r0: 0,
    r1: FAN_R0,
    mid: Math.PI / 2,
    cx,
    cy,
    path: `M ${cx - FAN_R0} ${cy} A ${FAN_R0} ${FAN_R0} 0 0 1 ${cx + FAN_R0} ${cy} Z`,
    band: "",
    fill: "",
    color: "",
    radial: false,
    left: false,
    size: 15,
    focus: true,
    fan: true,
  });

  return {
    nodes,
    edges: [],
    width: 2 * rMax + 2 * PAD,
    height: rMax + PAD + 24,
    focus: focusId,
    mode: "fan",
    center: { x: cx, y: cy },
  };
}
