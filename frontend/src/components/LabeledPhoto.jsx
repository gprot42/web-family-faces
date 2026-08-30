import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  FULLSCREEN_LABELS_EVENT,
  LABEL_LAYOUT_EVENT,
  LABEL_SIZE_EVENT,
  NAMETAG_EVENT,
  labelSizeInfo,
  readFullscreenLabels,
  readLabelLayout,
  readLabelSize,
  readNametag,
} from "../nametag.js";
import { PHOTO_CHANGE_EVENT, rememberPhotoRotation, showPhotoMenu } from "../photoMenu.js";
import { PhotoTagRow } from "./PhotoTags.jsx";
import { otherTags } from "../photoTags.js";

const FACE_TONES = ["#c45a32", "#1f8a7a", "#d4a017", "#3d7ec9", "#c44d7a", "#4a8f3a", "#7b5ea7", "#e07a2f"];

function clampPct(value) {
  return Math.min(99, Math.max(1, value));
}

/** Map a viewport point onto left/top % of the host's untransformed box (handles 90° photo rotate). */
export function pointToHostPct(host, clientX, clientY) {
  const box = host?.getBoundingClientRect?.();
  if (!box?.width || !box?.height) return { left: 50, top: 50 };
  const w = host.offsetWidth || box.width;
  const h = host.offsetHeight || box.height;
  const transform = host.ownerDocument?.defaultView?.getComputedStyle?.(host)?.transform;
  if (!transform || transform === "none") {
    return {
      left: clampPct((100 * (clientX - box.left)) / box.width),
      top: clampPct((100 * (clientY - box.top)) / box.height),
    };
  }
  const cx = box.left + box.width / 2;
  const cy = box.top + box.height / 2;
  const matrix = new DOMMatrix(transform);
  const local = new DOMMatrix([matrix.a, matrix.b, matrix.c, matrix.d, 0, 0]).inverse().transformPoint(
    new DOMPoint(clientX - cx, clientY - cy),
  );
  const scale = Math.hypot(box.width, box.height) / Math.hypot(w, h) || 1;
  return {
    left: clampPct(50 + (100 * local.x) / (w * scale)),
    top: clampPct(50 + (100 * local.y) / (h * scale)),
  };
}

export function faceTone(face, faces) {
  const mark = faces ? faceMark(face, faces) : null;
  if (mark) return FACE_TONES[(mark - 1) % FACE_TONES.length];
  const n = Math.abs(Math.trunc(Number(face?.id || 0))) || 0;
  return FACE_TONES[n % FACE_TONES.length];
}

export function boxesSameFace(a, b) {
  if (boxIou(a, b) >= 0.45) return true;
  const acx = ((Number(a?.x1) || 0) + (Number(a?.x2) || 0)) / 2;
  const acy = ((Number(a?.y1) || 0) + (Number(a?.y2) || 0)) / 2;
  const bcx = ((Number(b?.x1) || 0) + (Number(b?.x2) || 0)) / 2;
  const bcy = ((Number(b?.y1) || 0) + (Number(b?.y2) || 0)) / 2;
  const inA = acx >= (Number(b?.x1) || 0) && acx <= (Number(b?.x2) || 0) && acy >= (Number(b?.y1) || 0) && acy <= (Number(b?.y2) || 0);
  const inB = bcx >= (Number(a?.x1) || 0) && bcx <= (Number(a?.x2) || 0) && bcy >= (Number(a?.y1) || 0) && bcy <= (Number(a?.y2) || 0);
  return inA || inB;
}

export function boxIou(a, b) {
  const x1 = Math.max(Number(a?.x1) || 0, Number(b?.x1) || 0);
  const y1 = Math.max(Number(a?.y1) || 0, Number(b?.y1) || 0);
  const x2 = Math.min(Number(a?.x2) || 0, Number(b?.x2) || 0);
  const y2 = Math.min(Number(a?.y2) || 0, Number(b?.y2) || 0);
  const inter = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  if (!inter) return 0;
  const areaA = Math.max(0, (Number(a?.x2) || 0) - (Number(a?.x1) || 0)) * Math.max(0, (Number(a?.y2) || 0) - (Number(a?.y1) || 0));
  const areaB = Math.max(0, (Number(b?.x2) || 0) - (Number(b?.x1) || 0)) * Math.max(0, (Number(b?.y2) || 0) - (Number(b?.y1) || 0));
  const denom = areaA + areaB - inter;
  return denom ? inter / denom : 0;
}

export function displayFaces(faces) {
  const list = [...(faces || [])].filter(Boolean);
  const used = new Set();
  const kept = [];
  const rank = (f) => [
    f.assigned_how === "junk" ? 0 : 1,
    f.person_id ? 1 : 0,
    Number(f.det_score) || 0,
    -(Number(f.id) || 0),
  ];
  const better = (a, b) => {
    const ra = rank(a);
    const rb = rank(b);
    for (let i = 0; i < ra.length; i += 1) {
      if (ra[i] !== rb[i]) return ra[i] > rb[i];
    }
    return false;
  };
  for (let i = 0; i < list.length; i += 1) {
    const seed = list[i];
    if (used.has(seed.id)) continue;
    const group = [seed];
    for (let j = i + 1; j < list.length; j += 1) {
      const other = list[j];
      if (used.has(other.id)) continue;
      if (seed.photo_id != null && other.photo_id != null && String(seed.photo_id) !== String(other.photo_id)) {
        continue;
      }
      if (boxesSameFace(seed, other)) group.push(other);
    }
    let best = group[0];
    for (const face of group) {
      if (better(face, best)) best = face;
    }
    group.forEach((face) => used.add(face.id));
    kept.push(best);
  }
  return kept.sort((a, b) => (a.x1 || 0) - (b.x1 || 0) || (a.id || 0) - (b.id || 0));
}

export function overlayFaces(faces, { showHidden = false, hideUnknown = false } = {}) {
  return displayFaces(faces).filter((f) => {
    if (!showHidden && f.assigned_how === "junk") return false;
    if (!faceLabel(f)) return false;
    if (hideUnknown && isUnknownFace(f)) return false;
    return true;
  });
}

export function faceMarks(faces) {
  return displayFaces(faces).map((f, i) => ({ id: f.id, n: i + 1 }));
}

export function faceMark(face, faces) {
  return faceMarks(faces).find((item) => item.id === face?.id)?.n || null;
}

export function unnamedMarks(faces) {
  return overlayFaces(faces)
    .filter((f) => !f.person_id)
    .map((f, i) => ({ id: f.id, n: i + 1 }));
}

export function unnamedMark(face, faces) {
  return unnamedMarks(faces).find((item) => item.id === face?.id)?.n || null;
}

export function unnamedName(face, faces) {
  const n = unnamedMark(face, faces);
  return n ? `unnamed${n}` : "unnamed";
}

function toneVars(face, faces) {
  return { "--face-tone": faceTone(face, faces) };
}

export function isUnknownFace(face) {
  if (!face || face.assigned_how === "junk") return true;
  const name = String(face.person_name || "").trim();
  if (name && name !== "unnamed" && name !== "Unknown name of person" && !name.startsWith("Unknown name of person ")) {
    return false;
  }
  return !face.person_id;
}

export function faceLabel(face, short = false) {
  if (face.assigned_how === "junk") return "unnamed";
  if (face.quality === "unidentifiable" && !face.person_id) return "unnamed";
  const name = face.person_name || "unnamed";
  if (short && name.startsWith("Unknown name of person")) {
    const n = name.slice("Unknown name of person".length).trim();
    return n ? `Unknown ${n}` : "Unknown";
  }
  return name;
}

function useNametagPlacement() {
  const [place, setPlace] = useState(() => readNametag());
  useEffect(() => {
    const sync = () => setPlace(readNametag());
    window.addEventListener(NAMETAG_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(NAMETAG_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return place;
}

function useLabelLayout() {
  const [layout, setLayout] = useState(() => readLabelLayout());
  useEffect(() => {
    const sync = () => setLayout(readLabelLayout());
    window.addEventListener(LABEL_LAYOUT_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(LABEL_LAYOUT_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return layout;
}

function useLabelSize() {
  const [size, setSize] = useState(() => readLabelSize());
  useEffect(() => {
    const sync = () => setSize(readLabelSize());
    window.addEventListener(LABEL_SIZE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(LABEL_SIZE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return size;
}

function faceBoxStyle(face, w, h) {
  const padX = Math.max(4, (face.x2 - face.x1) * 0.24);
  const padY = Math.max(6, (face.y2 - face.y1) * 0.32);
  return {
    left: `${(100 * (face.x1 - padX)) / w}%`,
    top: `${(100 * (face.y1 - padY)) / h}%`,
    width: `${(100 * (face.x2 - face.x1 + padX * 2)) / w}%`,
    height: `${(100 * (face.y2 - face.y1 + padY * 2)) / h}%`,
  };
}

function tagFaceRect(face, w, h) {
  const fw = Math.max(1, face.x2 - face.x1);
  const fh = Math.max(1, face.y2 - face.y1);
  const padX = fw * 0.05;
  const padTop = fh * 0.08;
  const padBot = fh * 0.04;
  return {
    x1: (100 * (face.x1 - padX)) / w,
    y1: (100 * (face.y1 - padTop)) / h,
    x2: (100 * (face.x2 + padX)) / w,
    y2: (100 * (face.y2 + padBot)) / h,
  };
}

function bodyRect(faceRect) {
  const fw = Math.max(2, faceRect.x2 - faceRect.x1);
  const fh = Math.max(2, faceRect.y2 - faceRect.y1);
  const room = Math.max(0, 100 - faceRect.y2);
  const mul = fh > 28 ? 0.2 : fh > 16 ? 0.5 : fh > 9 ? 0.95 : 1.25;
  return {
    x1: faceRect.x1 - fw * 0.1,
    y1: faceRect.y2,
    x2: faceRect.x2 + fw * 0.1,
    y2: faceRect.y2 + Math.min(room, fh * mul),
  };
}

function centerOf(rect) {
  return { x: (rect.x1 + rect.x2) / 2, y: (rect.y1 + rect.y2) / 2 };
}

function overlapArea(a, b) {
  const x = Math.max(0, Math.min(a.x2, b.x2) - Math.max(a.x1, b.x1));
  const y = Math.max(0, Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1));
  return x * y;
}

function tagRect(left, top, tw, th, place, gap = 0.55) {
  if (place === "above") {
    return { x1: left - tw / 2, y1: top - th - gap, x2: left + tw / 2, y2: top - gap };
  }
  if (place === "below") {
    return { x1: left - tw / 2, y1: top + gap, x2: left + tw / 2, y2: top + gap + th };
  }
  if (place === "left") {
    return { x1: left - tw - gap, y1: top - th / 2, x2: left - gap, y2: top + th / 2 };
  }
  if (place === "right") {
    return { x1: left + gap, y1: top - th / 2, x2: left + gap + tw, y2: top + th / 2 };
  }
  return { x1: left - tw / 2, y1: top - th / 2, x2: left + tw / 2, y2: top + th / 2 };
}

function outOfBounds(rect) {
  return Math.max(0, -rect.x1) + Math.max(0, rect.x2 - 100) + Math.max(0, -rect.y1) + Math.max(0, rect.y2 - 100);
}

function clusterAround(index, rects) {
  const me = rects[index];
  const mc = centerOf(me);
  const reach = Math.max(14, Math.max(me.x2 - me.x1, me.y2 - me.y1) * 2.1);
  return rects.filter((other) => {
    const oc = centerOf(other);
    const gap = Math.max(other.x2 - other.x1, other.y2 - other.y1);
    return Math.hypot(oc.x - mc.x, oc.y - mc.y) <= reach + gap * 0.45;
  });
}

function someoneInFront(faceRect, faceRects, bodyRects = [], ownIndex = -1) {
  const fh = Math.max(2, faceRect.y2 - faceRect.y1);
  const fw = Math.max(2, faceRect.x2 - faceRect.x1);
  const faceHit = faceRects.some((other) => {
    if (other === faceRect) return false;
    const overlapX = Math.min(faceRect.x2, other.x2) - Math.max(faceRect.x1, other.x1);
    if (overlapX < fw * 0.18) return false;
    return other.y1 > faceRect.y1 + fh * 0.35 && other.y1 < faceRect.y2 + fh * 2.4;
  });
  if (faceHit) return true;
  const below = {
    x1: faceRect.x1 - fw * 0.2,
    y1: faceRect.y2,
    x2: faceRect.x2 + fw * 0.2,
    y2: faceRect.y2 + fh * 1.9,
  };
  return bodyRects.some((body, i) => {
    if (i === ownIndex) return false;
    return overlapArea(below, body) > fw * fh * 0.1;
  });
}

function shortName(label, allLabels = []) {
  const text = String(label || "").trim();
  if (!text) return text;
  const words = text.split(/\s+/);
  const first = words[0];
  const clash = allLabels.some((other) => {
    if (other === label) return false;
    return String(other || "").trim().split(/\s+/)[0] === first;
  });
  if (clash && words.length > 1) {
    const last = words[words.length - 1];
    const sameLast = allLabels.some((other) => {
      if (other === label) return false;
      const parts = String(other || "").trim().split(/\s+/);
      return parts[0] === first && parts[parts.length - 1] === last;
    });
    if (sameLast && words.length > 2 && words[1][0]) return `${first} ${words[1][0]}.`;
    if (last[0]) return `${first} ${last[0]}.`;
  }
  return first.length <= 12 ? first : `${first.slice(0, 11)}…`;
}

function groupFaceRows(faceRects) {
  if (!faceRects.length) return [];
  const items = faceRects.map((r, i) => ({ i, y: (r.y1 + r.y2) / 2, h: r.y2 - r.y1, r }));
  items.sort((a, b) => a.y - b.y);
  const heights = items.map((item) => item.h).sort((a, b) => a - b);
  const medH = heights[Math.floor(heights.length / 2)] || 8;
  const gap = Math.max(6.5, medH * 0.85);
  const rows = [];
  for (const item of items) {
    const row = rows[rows.length - 1];
    if (row && item.y - row[row.length - 1].y < gap) row.push(item);
    else rows.push([item]);
  }
  return rows;
}

function rowMeta(faceRects) {
  const rows = groupFaceRows(faceRects);
  const meta = new Map();
  rows.forEach((row, rowIndex) => {
    const sorted = [...row].sort((a, b) => centerOf(a.r).x - centerOf(b.r).x);
    const preferred =
      rows.length <= 1 ? "" : rowIndex === rows.length - 1 ? "below" : "above";
    sorted.forEach((item, rank) => {
      meta.set(item.i, { rowIndex, rank, size: sorted.length, rows: rows.length, preferred });
    });
  });
  return meta;
}

function outwardPlaces(faceRect, clusterRects, preferred, faceRects, blockedBelow) {
  const own = centerOf(faceRect);
  let cx = 0;
  let cy = 0;
  for (const rect of clusterRects) {
    const c = centerOf(rect);
    cx += c.x;
    cy += c.y;
  }
  cx /= clusterRects.length || 1;
  cy /= clusterRects.length || 1;
  const dx = own.x - cx;
  const dy = own.y - cy;
  const blocked = blockedBelow ?? someoneInFront(faceRect, faceRects || clusterRects);
  const ranked = [
    { place: "right", score: dx },
    { place: "left", score: -dx },
    { place: "below", score: blocked ? dy - 8 : dy },
    { place: "above", score: blocked ? -dy + 6 : -dy },
  ].sort((a, b) => b.score - a.score);
  const order = ranked.map((item) => item.place);
  const nearTop = faceRect.y1 < 9;
  const nearBottom = faceRect.y2 > 91;
  const nearLeft = faceRect.x1 < 8;
  const nearRight = faceRect.x2 > 92;
  const filtered = order.filter((place) => {
    if (place === "above" && nearTop) return false;
    if (place === "below" && (nearBottom || blocked)) return false;
    if (place === "left" && nearLeft) return false;
    if (place === "right" && nearRight) return false;
    return true;
  });
  const crowded = clusterRects.length >= 2;
  const crowdOrder = blocked ? ["above", "right", "left", "below"] : order;
  const head = [];
  if (preferred && preferred !== "on") {
    if (blocked && preferred === "below") head.push("above", "right", "left");
    else head.push(preferred);
  }
  const places = [...new Set([...head, ...filtered, ...crowdOrder, "above", "right", "left", "below"])];
  if (preferred === "on" && !crowded) places.push("on");
  return places;
}

function anchorFor(faceRect, place, standoff = 0) {
  const left = (faceRect.x1 + faceRect.x2) / 2;
  const topMid = (faceRect.y1 + faceRect.y2) / 2;
  if (place === "above") return { left, top: faceRect.y1 - standoff };
  if (place === "below") return { left, top: faceRect.y2 + standoff };
  if (place === "left") return { left: faceRect.x1 - standoff, top: topMid };
  if (place === "right") return { left: faceRect.x2 + standoff, top: topMid };
  return { left, top: topMid };
}

function collideRect(faceRect, generous) {
  const fw = Math.max(2, faceRect.x2 - faceRect.x1);
  const fh = Math.max(2, faceRect.y2 - faceRect.y1);
  const gx = generous ? 0.42 : 0.1;
  const gyTop = generous ? 0.28 : 0.08;
  const gyBot = generous ? 0.55 : 0.1;
  return {
    x1: faceRect.x1 - fw * gx,
    y1: faceRect.y1 - fh * gyTop,
    x2: faceRect.x2 + fw * gx,
    y2: faceRect.y2 + fh * gyBot,
  };
}

function headHalo(faceRect) {
  const fw = Math.max(2, faceRect.x2 - faceRect.x1);
  const fh = Math.max(2, faceRect.y2 - faceRect.y1);
  return {
    x1: faceRect.x1 - fw * 0.45,
    y1: faceRect.y1 - fh * 0.7,
    x2: faceRect.x2 + fw * 0.45,
    y2: faceRect.y2 + fh * 1.15,
  };
}

function eyeRect(faceRect) {
  const fw = Math.max(2, faceRect.x2 - faceRect.x1);
  const fh = Math.max(2, faceRect.y2 - faceRect.y1);
  return {
    x1: faceRect.x1 - fw * 0.12,
    y1: faceRect.y1 - fh * 0.06,
    x2: faceRect.x2 + fw * 0.12,
    y2: faceRect.y1 + fh * 0.72,
  };
}

function personBlob(faceRect, bodyRect) {
  const halo = headHalo(faceRect);
  return {
    x1: Math.min(halo.x1, bodyRect.x1 - 0.6),
    y1: halo.y1,
    x2: Math.max(halo.x2, bodyRect.x2 + 0.6),
    y2: Math.max(halo.y2, bodyRect.y2 + 1.2),
  };
}

function groupBounds(faceRects, bodyRects) {
  const xs = faceRects.flatMap((r) => [r.x1, r.x2]);
  const ys = [...faceRects.flatMap((r) => [r.y1, r.y2]), ...bodyRects.flatMap((r) => [r.y1, r.y2])];
  return {
    x1: Math.min(...xs),
    y1: Math.min(...ys),
    x2: Math.max(...xs),
    y2: Math.max(...ys),
  };
}

function coversEyes(rect, faceRect, slack = 0.12) {
  return overlapArea(rect, eyeRect(faceRect)) > slack;
}

function scoreTag(rect, tagObstacles, faceRects, bodyRects, ownFace, place, preferred, outward, blockedBelow, group = null, crowd = false, keepOffEyes = false) {
  const ac = centerOf(ownFace);
  const tc = { x: (rect.x1 + rect.x2) / 2, y: (rect.y1 + rect.y2) / 2 };
  let score = outOfBounds(rect) * (crowd ? 240 : 90);
  for (const other of tagObstacles) {
    const area = overlapArea(rect, other);
    if (area) score += area * (crowd ? 220 : 140);
    const oc = centerOf(other);
    const gap = Math.hypot(tc.x - oc.x, tc.y - oc.y);
    if (gap < 10) score += (10 - gap) * 6.5;
  }
  for (const face of faceRects) {
    const own = Math.abs(face.x1 - ownFace.x1) < 0.01 && Math.abs(face.y1 - ownFace.y1) < 0.01;
    const area = overlapArea(rect, collideRect(face, !own));
    if (area) score += area * (own ? 90 : 320);
    const halo = overlapArea(rect, headHalo(face));
    if (halo) score += halo * (own ? (crowd ? 240 : 160) : crowd ? 420 : 280);
    if (keepOffEyes) {
      const eyes = overlapArea(rect, eyeRect(face));
      if (eyes) score += eyes * (own ? 900 : 640);
    }
  }
  for (let i = 0; i < bodyRects.length; i += 1) {
    const body = bodyRects[i];
    const face = faceRects[i];
    const own = face && Math.abs(face.x1 - ownFace.x1) < 0.01 && Math.abs(face.y1 - ownFace.y1) < 0.01;
    const area = overlapArea(rect, body);
    if (area) score += area * (own ? (crowd ? 190 : 18) : 170);
    if (crowd && face) {
      const blob = overlapArea(rect, personBlob(face, body));
      if (blob) score += blob * (own ? 160 : 260);
    }
  }
  if (group) {
    const inner = { x1: group.x1 + 1.5, y1: group.y1 + 2.8, x2: group.x2 - 1.5, y2: group.y2 - 2.2 };
    const buried = overlapArea(rect, inner);
    if (buried) score += buried * (crowd ? 120 : 55);
  }
  const dist = Math.hypot(tc.x - ac.x, tc.y - ac.y);
  const faceSize = Math.max(ownFace.x2 - ownFace.x1, ownFace.y2 - ownFace.y1);
  score += dist * (crowd ? 0.28 : 0.85);
  if (dist > faceSize * 2.4 + 4) score += (dist - faceSize * 2.4) * (crowd ? 0.55 : 3.2);
  if (place === preferred && !(blockedBelow && place === "below")) score -= 16;
  if (place === outward[0]) score -= 2.2;
  if (place === outward[1]) score -= 0.8;
  if (place === "above") score -= blockedBelow ? 3.6 : 0.5;
  if (place === "below" && blockedBelow) score += 28;
  if (place === "on") score += preferred === "on" ? 2 : 28;
  if (keepOffEyes) {
    if (place === "on") score += 90;
    if (place === "above") score -= 22;
  }
  return score;
}

function pinOf(face, pins) {
  const pin = pins?.[face?.id];
  if (!pin) return null;
  const left = Number(pin.left);
  const top = Number(pin.top);
  if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
  return { left, top };
}

export function layoutTags(faces, w, h, preferredPlace, keepUnnamed = false, pins = null, layoutId = "smart", sizeId = "small") {
  const mode = ["smart", "rows", "halo", "numbers"].includes(layoutId) ? layoutId : "smart";
  const scale = labelSizeInfo(sizeId).scale;
  const crowd = faces.length >= 6 || mode === "halo" || mode === "rows";
  const compactUnnamed = faces.length >= 5;
  const numbers = mode === "numbers";
  const keepOffEyes = !numbers;
  const faceRects = faces.map((face) => tagFaceRect(face, w, h));
  const bodyRects = faceRects.map((rect) => bodyRect(rect));
  const rows = rowMeta(faceRects);
  const namedLabels = faces.filter((face) => !isUnknownFace(face)).map((face) => faceLabel(face, true));
  const jobs = faces
    .map((face, index) => {
      const named = !isUnknownFace(face);
      const n = faceMark(face, faces);
      const compact = numbers || (compactUnnamed && !named);
      const full = named ? faceLabel(face, true) : unnamedName(face, faces);
      const label = compact ? String(n || "") : named && crowd ? shortName(full, namedLabels) : full;
      const chars = String(label || "").length;
      const tw =
        (compact
          ? 3.8
          : Math.min(crowd ? 20 : 32, Math.max(6.2, chars * (crowd ? 0.72 : 0.78) + (crowd ? 7.2 : 6.2)))) * scale;
      const th = (compact ? 3.4 : crowd ? 4.0 : 4.4) * scale;
      return { face, index, named, label, tw, th, compact, faceRect: faceRects[index] };
    })
    .filter((job) => job.named || keepUnnamed || !job.compact);
  jobs.sort((a, b) => {
    const ra = rows.get(a.index);
    const rb = rows.get(b.index);
    const rowDelta = (ra?.rowIndex ?? 0) - (rb?.rowIndex ?? 0);
    if (rowDelta) return rowDelta;
    const ac = centerOf(a.faceRect);
    const bc = centerOf(b.faceRect);
    const aEdge = Math.min(ac.x, 100 - ac.x);
    const bEdge = Math.min(bc.x, 100 - bc.x);
    return aEdge - bEdge || ac.x - bc.x;
  });

  const standOffs = crowd ? [0.2, 0.6, 1.2, 2.1, 3.2, 4.6, 6.0] : [0.15, 0.45, 1.0, 1.8, 2.8, 4.4, 6.0];
  const slides = crowd ? [0, -1.6, 1.6, -3.2, 3.2, -5.0, 5.0, -6.8, 6.8] : [0, -1.6, 1.6, -3.2, 3.2, -5.0, 5.0];
  const group = groupBounds(faceRects, bodyRects);

  function pickPlace(job, tagObstacles) {
    const row = rows.get(job.index);
    const rowPref = numbers
      ? "on"
      : mode === "rows" && row?.preferred
        ? row.preferred
        : keepOffEyes
          ? "above"
          : preferredPlace;
    const cluster = clusterAround(job.index, faceRects);
    const blockedBelow = someoneInFront(job.faceRect, faceRects, bodyRects, job.index);
    const outward = outwardPlaces(job.faceRect, cluster, rowPref, faceRects, blockedBelow).filter(
      (side) => !keepOffEyes || side !== "on",
    );
    if (keepOffEyes && !outward.includes("above")) outward.unshift("above");
    const faceSize = Math.max(job.faceRect.x2 - job.faceRect.x1, job.faceRect.y2 - job.faceRect.y1);
    const maxOff = Math.max(crowd ? 3.6 : 2.8, faceSize * (crowd ? 1.15 : 0.75));
    const stagger = crowd && row && row.size >= 3 && (rowPref === "above" || rowPref === "below") ? (row.rank % 2) * 2.8 : 0;
    let best = null;
    let bestScore = Infinity;
    let bestSafe = null;
    let bestSafeScore = Infinity;
    for (const side of outward) {
      for (const standoff of standOffs) {
        const extra = side === "above" || side === "below" ? stagger : 0;
        if (keepOffEyes && side === "above" && standoff < 1.4) continue;
        if (standoff + extra > maxOff + 2.2) continue;
        for (const slide of slides) {
          const body = bodyRects[job.index];
          const anchor =
            side === "below" && rowPref === "below" && body && faceSize < 14 && !blockedBelow
              ? { left: (job.faceRect.x1 + job.faceRect.x2) / 2, top: body.y2 + standoff + extra }
              : anchorFor(job.faceRect, side, standoff + extra);
          let left = anchor.left;
          let top = anchor.top;
          if (side === "above" || side === "below" || side === "on") left += slide;
          else top += slide;
          left = Math.min(100 - job.tw / 2 - 0.7, Math.max(job.tw / 2 + 0.7, left));
          top = Math.min(97.5, Math.max(2.5, top));
          const rect = tagRect(left, top, job.tw, job.th, side);
          const score = scoreTag(
            rect,
            tagObstacles,
            faceRects,
            bodyRects,
            job.faceRect,
            side,
            rowPref,
            outward,
            blockedBelow,
            group,
            crowd,
            keepOffEyes,
          );
          const next = { ...job, left, top, place: side, rect, score };
          if (score < bestScore) {
            bestScore = score;
            best = next;
          }
          if (!keepOffEyes || !coversEyes(rect, job.faceRect)) {
            if (score < bestSafeScore) {
              bestSafeScore = score;
              bestSafe = next;
            }
          }
        }
      }
    }
    return bestSafe || best;
  }

  function clampAnchor(left, top, tw, th, place) {
    let l = left;
    let t = top;
    const minY = keepOffEyes && place === "above" ? -1.2 : 1.3;
    for (let i = 0; i < 5; i += 1) {
      const rect = tagRect(l, t, tw, th, place);
      if (rect.x1 < 1.3) l += 1.3 - rect.x1;
      if (rect.x2 > 98.7) l -= rect.x2 - 98.7;
      if (rect.y1 < minY) t += minY - rect.y1;
      if (rect.y2 > 98.7) t -= rect.y2 - 98.7;
    }
    return { left: l, top: t };
  }

  function pickCrowdSlot(job, tagObstacles) {
    const fx = (job.faceRect.x1 + job.faceRect.x2) / 2;
    const fy = (job.faceRect.y1 + job.faceRect.y2) / 2;
    const span = Math.max(8, maxFaceY - minFaceY);
    const skyRoom = minFaceY >= 10;
    const floorRoom = maxFaceY <= 85;
    const shallow = nRows <= 2 && span <= 42;
    let nearTop = fy <= minFaceY + span * 0.4;
    let nearBot = fy >= minFaceY + span * 0.55;
    if (shallow && skyRoom) {
      nearTop = true;
      nearBot = false;
    } else if (shallow && floorRoom && !skyRoom) {
      nearTop = false;
      nearBot = true;
    }
    const xSlides = [0, -job.tw * 0.45, job.tw * 0.45, -job.tw * 0.95, job.tw * 0.95, -job.tw * 1.5, job.tw * 1.5];
    const localOff = keepOffEyes ? [1.6, 2.6, 4.0, 5.6, 7.4, 9.2] : [0.5, 1.4, 2.6, 4.0, 5.6, 7.4];
    const localSlide = [0, -2.2, 2.2, -4.4, 4.4, -6.8, 6.8];
    const candidates = [];
    const localSides = keepOffEyes ? ["above"] : ["above", "below", "left", "right"];
    for (const side of localSides) {
      for (const off of localOff) {
        for (const slide of localSlide) {
          const anchor = anchorFor(job.faceRect, side, off);
          candidates.push({
            place: side,
            left: side === "above" || side === "below" ? anchor.left + slide : anchor.left,
            top: side === "left" || side === "right" ? anchor.top + slide : anchor.top,
          });
        }
      }
    }
    if (keepOffEyes) {
      const hair = job.faceRect.y1;
      const hairOff = [1.8, 2.8, 4.0, 5.6, 7.4, 9.4];
      for (const off of hairOff) {
        for (const dx of xSlides) {
          candidates.push({ place: "above", left: fx + dx, top: hair - off });
        }
      }
    }
    const bandX = (y, place) => {
      for (const dx of xSlides) candidates.push({ place, left: fx + dx, top: y });
    };
    if (nearTop) {
      bandX(topY, "above");
      bandX(Math.max(2.6, topY - 3.2), "above");
    }
    if (nearBot && !keepOffEyes) {
      bandX(botY, "below");
      bandX(Math.min(96.8, botY + 3.6), "below");
    }
    if (keepOffEyes && nearBot) {
      bandX(Math.max(2.6, job.faceRect.y1 - Math.max(2.4, job.th + 1.2)), "above");
    }
    const rightX = Math.min(99.1 - job.tw / 2, group.x2 + Math.max(2.2, job.tw * 0.5 + 1.4));
    const leftX = Math.max(job.tw / 2 + 0.9, group.x1 - Math.max(2.2, job.tw * 0.5 + 1.4));
    const sideYs = keepOffEyes
      ? [job.faceRect.y1 - 0.8, job.faceRect.y1 - job.th * 0.9, job.faceRect.y1 - job.th * 1.8]
      : [fy, fy - job.th * 1.4, fy + job.th * 1.4, fy - job.th * 2.8, fy + job.th * 2.8];
    if (rightX > group.x2 + 0.3) {
      for (const top of sideYs) candidates.push({ place: "right", left: rightX, top });
    }
    if (leftX < group.x1 - 0.3) {
      for (const top of sideYs) candidates.push({ place: "left", left: leftX, top });
    }
    let best = null;
    let bestScore = Infinity;
    let bestSafe = null;
    let bestSafeScore = Infinity;
    const preferred = keepOffEyes ? "above" : nearBot ? "below" : nearTop ? "above" : "above";
    for (const cand of candidates) {
      const clamped = clampAnchor(cand.left, cand.top, job.tw, job.th, cand.place);
      const rect = tagRect(clamped.left, clamped.top, job.tw, job.th, cand.place);
      let score = scoreTag(
        rect,
        tagObstacles,
        faceRects,
        bodyRects,
        job.faceRect,
        cand.place,
        preferred,
        [cand.place],
        false,
        group,
        true,
        keepOffEyes,
      );
      score += Math.abs(clamped.left - fx) * 0.35;
      if (keepOffEyes) {
        score += Math.abs(clamped.top - job.faceRect.y1) * 0.1;
        if (cand.place === "above") score -= 40;
        const faceH = Math.max(2, job.faceRect.y2 - job.faceRect.y1);
        if (clamped.top > job.faceRect.y1 + faceH * 0.12) score += 55;
      } else {
        score += Math.abs(clamped.top - fy) * 0.22;
      }
      const next = { ...job, left: clamped.left, top: clamped.top, place: cand.place, rect, score };
      if (score < bestScore) {
        bestScore = score;
        best = next;
      }
      if (!keepOffEyes || !coversEyes(rect, job.faceRect)) {
        if (score < bestSafeScore) {
          bestSafeScore = score;
          bestSafe = next;
        }
      }
    }
    return bestSafe || best;
  }

  function pickHaloSlot(job, tagObstacles) {
    const fx = (job.faceRect.x1 + job.faceRect.x2) / 2;
    const fy = (job.faceRect.y1 + job.faceRect.y2) / 2;
    const gy = (group.y1 + group.y2) / 2;
    const sky = Math.max(2.8, group.y1 - Math.max(3.2, job.th + 1.4));
    const floor = Math.min(96.8, group.y2 + Math.max(3.6, job.th + 1.8));
    const leftX = Math.max(job.tw / 2 + 1, group.x1 - Math.max(2.4, job.tw * 0.55 + 1.6));
    const rightX = Math.min(99 - job.tw / 2, group.x2 + Math.max(2.4, job.tw * 0.55 + 1.6));
    const xSlides = [0, -job.tw * 0.5, job.tw * 0.5, -job.tw, job.tw, -job.tw * 1.45, job.tw * 1.45];
    const candidates = [];
    const preferTop = fy <= gy + 1;
    for (const dx of xSlides) {
      candidates.push({ place: "above", left: fx + dx, top: sky });
      candidates.push({ place: "below", left: fx + dx, top: floor });
    }
    const sideYs = [fy, fy - job.th * 1.5, fy + job.th * 1.5, fy - job.th * 3, fy + job.th * 3];
    for (const top of sideYs) {
      candidates.push({ place: "left", left: leftX, top });
      candidates.push({ place: "right", left: rightX, top });
    }
    let best = null;
    let bestScore = Infinity;
    let bestSafe = null;
    let bestSafeScore = Infinity;
    for (const cand of candidates) {
      const clamped = clampAnchor(cand.left, cand.top, job.tw, job.th, cand.place);
      const rect = tagRect(clamped.left, clamped.top, job.tw, job.th, cand.place);
      let score = scoreTag(
        rect,
        tagObstacles,
        faceRects,
        bodyRects,
        job.faceRect,
        cand.place,
        preferTop ? "above" : "below",
        [cand.place],
        false,
        group,
        true,
        keepOffEyes,
      );
      const inner = { x1: group.x1 + 2, y1: group.y1 + 3, x2: group.x2 - 2, y2: group.y2 - 2.4 };
      score += overlapArea(rect, inner) * 380;
      if (preferTop && cand.place === "above") score -= 18;
      if (!preferTop && cand.place === "below") score -= 18;
      score += Math.abs(clamped.left - fx) * 0.22;
      const next = { ...job, left: clamped.left, top: clamped.top, place: cand.place, rect, score };
      if (score < bestScore) {
        bestScore = score;
        best = next;
      }
      if (!keepOffEyes || !coversEyes(rect, job.faceRect)) {
        if (score < bestSafeScore) {
          bestSafeScore = score;
          bestSafe = next;
        }
      }
    }
    return bestSafe || best;
  }

  const placed = [];
  const pinnedIds = new Set();
  for (const job of jobs) {
    const pin = pinOf(job.face, pins);
    if (!pin) continue;
    const rect = tagRect(pin.left, pin.top, job.tw, job.th, "manual");
    placed.push({ ...job, left: pin.left, top: pin.top, place: "manual", rect });
    pinnedIds.add(job.face.id);
  }
  const nRows = jobs[0] ? rows.get(jobs[0].index)?.rows || 1 : 1;
  const freeJobs = jobs.filter((job) => !pinnedIds.has(job.face.id));
  const usePerimeter = mode === "smart" && crowd;
  const useHalo = mode === "halo";
  const minFaceY = Math.min(...faceRects.map((r) => r.y1));
  const maxFaceY = Math.max(...faceRects.map((r) => r.y2));
  const medH =
    faceRects.map((r) => r.y2 - r.y1).sort((a, b) => a - b)[Math.floor(faceRects.length / 2)] || 6;
  const topY = Math.max(3.2, minFaceY - Math.max(6.8, medH * 1.05));
  const botY = Math.min(94.8, Math.max(maxFaceY + Math.max(8, medH * 1.35), group.y2 + 4.2));

  function placeCrowdJob(job, obstacles) {
    if (useHalo) return pickHaloSlot(job, obstacles) || pickPlace(job, obstacles);
    return pickCrowdSlot(job, obstacles) || pickPlace(job, obstacles);
  }

  if (numbers) {
    freeJobs.forEach((job) => {
      const anchor = anchorFor(job.faceRect, "on", 0);
      const clamped = clampAnchor(anchor.left, anchor.top, job.tw, job.th, "on");
      const rect = tagRect(clamped.left, clamped.top, job.tw, job.th, "on");
      placed.push({ ...job, left: clamped.left, top: clamped.top, place: "on", rect });
    });
  } else if (usePerimeter || useHalo) {
    const ordered = [...freeJobs].sort((a, b) => {
      const ac = centerOf(a.faceRect);
      const bc = centerOf(b.faceRect);
      const aEdge = Math.min(ac.x, 100 - ac.x, ac.y - minFaceY, maxFaceY - ac.y);
      const bEdge = Math.min(bc.x, 100 - bc.x, bc.y - minFaceY, maxFaceY - bc.y);
      return aEdge - bEdge;
    });
    ordered.forEach((job) => {
      const best = placeCrowdJob(job, placed.map((item) => item.rect));
      if (best) placed.push(best);
    });
  } else {
    for (const job of freeJobs) {
      const best = pickPlace(
        job,
        placed.map((item) => item.rect),
      );
      if (best) placed.push(best);
    }
  }

  const free = placed.filter((item) => item.place !== "manual");
  if (!numbers) for (let pass = 0; pass < (crowd ? 3 : 2); pass += 1) {
    if (usePerimeter || useHalo) {
      const ordered = [...free].sort((a, b) => {
        const ac = centerOf(a.faceRect);
        const bc = centerOf(b.faceRect);
        const aEdge = Math.min(ac.x, 100 - ac.x, ac.y - minFaceY, maxFaceY - ac.y);
        const bEdge = Math.min(bc.x, 100 - bc.x, bc.y - minFaceY, maxFaceY - bc.y);
        return aEdge - bEdge;
      });
      ordered.forEach((current) => {
        const obstacles = placed.filter((item) => item.face.id !== current.face.id).map((item) => item.rect);
        const next = placeCrowdJob(current, obstacles);
        if (!next) return;
        const idx = placed.findIndex((item) => item.face.id === current.face.id);
        if (idx >= 0) placed[idx] = next;
        const fi = free.findIndex((item) => item.face.id === current.face.id);
        if (fi >= 0) free[fi] = next;
      });
    } else {
      for (let i = 0; i < free.length; i += 1) {
        const current = free[i];
        const obstacles = placed.filter((item) => item.face.id !== current.face.id).map((item) => item.rect);
        const next = pickPlace(current, obstacles);
        if (!next) continue;
        const idx = placed.findIndex((item) => item.face.id === current.face.id);
        if (idx >= 0) placed[idx] = next;
        free[i] = next;
      }
    }
  }

  if (usePerimeter || useHalo) {
    for (let pass = 0; pass < 10; pass += 1) {
      let moved = false;
      for (let i = 0; i < placed.length; i += 1) {
        const a = placed[i];
        if (a.place === "manual") continue;
        for (let j = 0; j < placed.length; j += 1) {
          if (i === j) continue;
          if (overlapArea(a.rect, placed[j].rect) < 0.35) continue;
          const dir = a.top >= placed[j].top ? 1 : -1;
          const nextTop = a.top + dir * (a.th + 0.55);
          const clamped = clampAnchor(a.left, nextTop, a.tw, a.th, a.place);
          const rect = tagRect(clamped.left, clamped.top, a.tw, a.th, a.place);
          if (overlapArea(rect, headHalo(a.faceRect)) > 1.2) continue;
          if (keepOffEyes && coversEyes(rect, a.faceRect)) continue;
          const hitsOtherFace = faceRects.some((face) => {
            const own = Math.abs(face.x1 - a.faceRect.x1) < 0.01 && Math.abs(face.y1 - a.faceRect.y1) < 0.01;
            return !own && overlapArea(rect, headHalo(face)) > 0.6;
          });
          if (hitsOtherFace) continue;
          a.left = clamped.left;
          a.top = clamped.top;
          a.rect = rect;
          moved = true;
        }
      }
      if (!moved) break;
    }
  }

  placed.sort((a, b) => a.index - b.index);
  return placed.map((item) => ({
    left: item.left,
    top: item.top,
    stacked: false,
    items: [
      {
        face: item.face,
        left: item.left,
        top: item.top,
        place: item.place,
        compact: item.compact,
        label: item.label,
      },
    ],
  }));
}

function normPct(a, b) {
  return {
    x1: Math.min(a.left, b.left),
    y1: Math.min(a.top, b.top),
    x2: Math.max(a.left, b.left),
    y2: Math.max(a.top, b.top),
  };
}

export default function LabeledPhoto({
  photo,
  src,
  fallbackSrc,
  to,
  toState,
  activeId,
  onFaceClick,
  onPhotoClick,
  maxHeight,
  fit,
  tagsBelow,
  overlayTags,
  hideUnknown,
  showHidden,
  showUnnamed,
  movable,
  onTagMove,
  selecting,
  selectingBusy,
  onRegionSelect,
  priority,
}) {
  const place = useNametagPlacement();
  const labelLayout = useLabelLayout();
  const labelSize = useLabelSize();
  const [labelsOn, setLabelsOn] = useState(() => readFullscreenLabels());
  const [rotation, setRotation] = useState(() => Number(photo.rotation) || 0);
  useEffect(() => {
    setRotation(Number(photo.rotation) || 0);
  }, [photo.id, photo.rotation]);
  useEffect(() => {
    function sync() {
      setLabelsOn(readFullscreenLabels());
    }
    window.addEventListener(FULLSCREEN_LABELS_EVENT, sync);
    return () => window.removeEventListener(FULLSCREEN_LABELS_EVENT, sync);
  }, []);
  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next || Number(next.id) !== Number(photo.id)) return;
      if (next.hidden) return;
      if (next.rotation != null) setRotation(Number(next.rotation) || 0);
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, [photo.id]);
  const rot = ((rotation % 360) + 360) % 360;
  useEffect(() => {
    if (photo?.id) rememberPhotoRotation(photo.id, rot);
  }, [photo?.id, rot]);
  const w = photo.width || 1;
  const h = photo.height || 1;
  const swapped = rot === 90 || rot === 270;
  const dw = swapped ? h : w;
  const dh = swapped ? w : h;
  const rotVars = {
    "--photo-ar": dw / dh,
    "--ow": w,
    "--oh": h,
    "--dw": dw,
    "--dh": dh,
    "--rot": `${rot}deg`,
    "--unrot": `${-rot}deg`,
  };
  const allFaces = useMemo(() => displayFaces(photo.faces || []), [photo.faces]);
  const faces = useMemo(
    () => overlayFaces(photo.faces || [], { showHidden, hideUnknown }),
    [photo.faces, showHidden, hideUnknown],
  );
  const overlay = overlayTags === undefined ? labelsOn : !!overlayTags;
  const [dragPin, setDragPin] = useState(null);
  const [localPins, setLocalPins] = useState({});
  const [honorSavedPins, setHonorSavedPins] = useState(() => readLabelLayout() !== "smart");
  const drag = useRef(null);
  const skipClick = useRef(false);
  const pick = useRef(null);
  const pickHostRef = useRef(null);
  const [pickRect, setPickRect] = useState(null);
  useEffect(() => {
    setLocalPins({});
    setDragPin(null);
    setHonorSavedPins(readLabelLayout() !== "smart");
  }, [photo.id]);
  useEffect(() => {
    function onLayout() {
      setLocalPins({});
      setDragPin(null);
      setHonorSavedPins(false);
    }
    window.addEventListener(LABEL_LAYOUT_EVENT, onLayout);
    return () => window.removeEventListener(LABEL_LAYOUT_EVENT, onLayout);
  }, []);
  const pins = useMemo(() => {
    const out = {};
    if (honorSavedPins) {
      for (const f of faces) {
        if (f.tag_x == null || f.tag_y == null) continue;
        const left = Number(f.tag_x);
        const top = Number(f.tag_y);
        if (Number.isFinite(left) && Number.isFinite(top)) out[f.id] = { left, top };
      }
    }
    Object.assign(out, localPins);
    if (dragPin) out[dragPin.id] = { left: dragPin.left, top: dragPin.top };
    return out;
  }, [faces, localPins, dragPin, honorSavedPins]);
  const tagGroups = useMemo(
    () => (overlay ? layoutTags(faces, w, h, place, Boolean(showUnnamed || showHidden), pins, labelLayout, labelSize) : []),
    [overlay, faces, w, h, place, showUnnamed, showHidden, pins, labelLayout, labelSize],
  );
  const taggedIds = useMemo(() => {
    const ids = new Set();
    for (const group of tagGroups) {
      for (const item of group.items) ids.add(item.face.id);
    }
    return ids;
  }, [tagGroups]);

  function tagHost(node) {
    return node?.closest(".labeled-inner, .labeled-rot") || node?.offsetParent || node?.parentElement;
  }

  function clientToPct(host, clientX, clientY) {
    return pointToHostPct(host, clientX, clientY);
  }

  function endTagWindowDrag() {
    const d = drag.current;
    if (!d?.move) return;
    window.removeEventListener("pointermove", d.move);
    window.removeEventListener("pointerup", d.up);
    window.removeEventListener("pointercancel", d.up);
  }

  function onTagPointerMove(event) {
    const d = drag.current;
    if (!d || d.id == null) return;
    event.preventDefault();
    const dx = event.clientX - d.startX;
    const dy = event.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true;
    const grab = clientToPct(d.host, event.clientX, event.clientY);
    const next = {
      left: Math.min(99, Math.max(1, grab.left - (d.grabDx || 0))),
      top: Math.min(99, Math.max(1, grab.top - (d.grabDy || 0))),
    };
    d.pin = next;
    setDragPin({ id: d.id, left: next.left, top: next.top });
  }

  function onTagPointerUp(event) {
    const d = drag.current;
    if (!d) return;
    endTagWindowDrag();
    const moved = d.moved;
    const face = d.face;
    const pin = d.pin;
    drag.current = null;
    setDragPin(null);
    skipClick.current = true;
    if (moved && pin) {
      event.preventDefault();
      event.stopPropagation();
      setLocalPins((cur) => ({ ...cur, [face.id]: { left: pin.left, top: pin.top } }));
      onTagMove?.(face, pin);
      return;
    }
    onFaceClick?.(face);
  }

  function onTagPointerDown(event, face, start) {
    if (selecting) return;
    if (!movable) return;
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const host = tagHost(event.currentTarget);
    const box = event.currentTarget.getBoundingClientRect();
    const center = clientToPct(host, box.left + box.width / 2, box.top + box.height / 2);
    const grab = clientToPct(host, event.clientX, event.clientY);
    const move = (ev) => onTagPointerMove(ev);
    const up = (ev) => onTagPointerUp(ev);
    drag.current = {
      id: face.id,
      face,
      host,
      origin: { left: start.left, top: start.top },
      grabDx: grab.left - center.left,
      grabDy: grab.top - center.top,
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
      move,
      up,
    };
    try {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } catch {
      /* synthetic pointer or already released */
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  function onTagClick(event, face) {
    event.preventDefault();
    event.stopPropagation();
    if (selecting) return;
    if (skipClick.current) {
      skipClick.current = false;
      return;
    }
    onFaceClick?.(face);
  }

  function pickHost(node) {
    const inner = node?.closest?.(".labeled-inner") || node;
    return inner?.querySelector?.(":scope > .labeled-rot") || inner;
  }

  function releasePickCapture(event) {
    const el = event?.currentTarget || pick.current?.target || pickHostRef.current;
    const pid = event?.pointerId ?? pick.current?.pointerId;
    if (!el || pid == null) return;
    try {
      el.releasePointerCapture?.(pid);
    } catch {
      /* already released */
    }
  }

  function onPickDown(event) {
    if (!selecting || selectingBusy) return;
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const host = pickHost(event.currentTarget);
    const start = clientToPct(host, event.clientX, event.clientY);
    pick.current = {
      host,
      start,
      moved: false,
      pointerId: event.pointerId,
      target: event.currentTarget,
    };
    setPickRect({ x1: start.left, y1: start.top, x2: start.left, y2: start.top });
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function onPickMove(event) {
    const d = pick.current;
    if (!d) return;
    const now = clientToPct(d.host, event.clientX, event.clientY);
    if (Math.hypot(now.left - d.start.left, now.top - d.start.top) > 0.5) d.moved = true;
    d.end = now;
    setPickRect(normPct(d.start, now));
  }

  function onPickUp(event) {
    releasePickCapture(event);
    const d = pick.current;
    pick.current = null;
    if (!d) return;
    const rect = d.end ? normPct(d.start, d.end) : null;
    if (!d.moved || !rect) {
      if (!selectingBusy) setPickRect(null);
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    skipClick.current = true;
    const bw = ((rect.x2 - rect.x1) / 100) * w;
    const bh = ((rect.y2 - rect.y1) / 100) * h;
    if (Math.min(bw, bh) < 8) {
      setPickRect(null);
      return;
    }
    setPickRect(rect);
    onRegionSelect?.({
      x1: (rect.x1 / 100) * w,
      y1: (rect.y1 / 100) * h,
      x2: (rect.x2 / 100) * w,
      y2: (rect.y2 / 100) * h,
    });
  }

  useEffect(() => {
    if (selecting) return undefined;
    releasePickCapture();
    pick.current = null;
    setPickRect(null);
    return undefined;
  }, [selecting]);

  function onTagDoubleClick(event, face) {
    if (!movable) return;
    event.preventDefault();
    event.stopPropagation();
    setLocalPins((cur) => {
      const next = { ...cur };
      delete next[face.id];
      return next;
    });
    setDragPin(null);
    onTagMove?.(face, null);
  }

  const picture = (
    <>
      <img
        src={src}
        alt={photo.filename || ""}
        draggable={false}
        onDragStart={(e) => e.preventDefault()}
        fetchPriority={priority ? "high" : undefined}
        decoding="async"
        style={maxHeight && !fit ? { maxHeight } : undefined}
        onError={(e) => {
          if (fallbackSrc && e.currentTarget.src !== fallbackSrc) e.currentTarget.src = fallbackSrc;
        }}
      />
      {overlay
        ? faces.map((f) => {
            const n = faceMark(f, faces);
            const tagged = taggedIds.has(f.id);
            return (
              <button
                key={`box-${f.id}`}
                type="button"
                className={`face-box ${f.id === activeId ? "active" : ""}`}
                style={{ ...faceBoxStyle(f, w, h), ...toneVars(f, faces) }}
                data-face-id={f.id}
                data-n={tagged ? undefined : n || undefined}
                aria-label={n ? `${n}. ${faceLabel(f, true) || "Face"}` : faceLabel(f, true) || "Face"}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (selecting) return;
                  onFaceClick?.(f);
                }}
              />
            );
          })
        : null}
      {overlay
        ? tagGroups.flatMap((group) =>
            group.items.map((item) => {
              const f = item.face;
              const unknown = isUnknownFace(f);
              const label = item.label || (unknown ? unnamedName(f, faces) : faceLabel(f, true));
              const n = faceMark(f, faces);
              const dragging = dragPin?.id === f.id;
              return (
                <button
                  key={`tag-${f.id}`}
                  type="button"
                  className={`nametag place-${item.place} ${unknown ? "unnamed" : "named"} ${item.compact ? "compact" : ""} ${f.id === activeId ? "active" : ""} ${movable ? "movable" : ""} ${dragging ? "dragging" : ""}`}
                  style={{ left: `${item.left}%`, top: `${item.top}%`, ...toneVars(f, faces) }}
                  data-face-id={f.id}
                  aria-label={
                    (unknown ? unnamedName(f, faces) : n ? `${n}. ${label || "Face"}` : label || "Face") +
                    (f.comment ? `. ${f.comment}` : "")
                  }
                  title={f.comment || undefined}
                  onPointerDown={(e) => onTagPointerDown(e, f, { left: item.left, top: item.top })}
                  onPointerMove={onTagPointerMove}
                  onPointerUp={onTagPointerUp}
                  onPointerCancel={onTagPointerUp}
                  onDoubleClick={(e) => onTagDoubleClick(e, f)}
                  onClick={(e) => onTagClick(e, f)}
                >
                  {item.compact ? (
                    <span className="nametag-n">{n || label}</span>
                  ) : (
                    <>
                      {n ? <span className="nametag-n">{n}</span> : null}
                      {label}
                    </>
                  )}
                </button>
              );
            }),
          )
        : null}
    </>
  );

  const band = pickRect ? (
    <div
      className={`face-select-box${selectingBusy ? " busy" : ""}`}
      style={{
        left: `${pickRect.x1}%`,
        top: `${pickRect.y1}%`,
        width: `${Math.max(0, pickRect.x2 - pickRect.x1)}%`,
        height: `${Math.max(0, pickRect.y2 - pickRect.y1)}%`,
      }}
    />
  ) : null;

  const img = (
    <div
      ref={pickHostRef}
      className={`labeled-inner place-${place} ${fit ? "fit" : ""} ${rot ? `rot-${rot}` : ""} ${faces.length >= 8 ? "crowd" : ""}`}
      style={fit || rot ? rotVars : undefined}
      onPointerDown={selecting ? onPickDown : undefined}
      onPointerMove={selecting ? onPickMove : undefined}
      onPointerUp={selecting ? onPickUp : undefined}
      onPointerCancel={selecting ? onPickUp : undefined}
    >
      {rot ? (
        <div className="labeled-rot">
          {picture}
          {band}
        </div>
      ) : (
        <>
          {picture}
          {band}
        </>
      )}
    </div>
  );

  const seen = new Set();
  const chips = [];
  const ordered = [...faces].sort((a, b) => (a.x1 || 0) - (b.x1 || 0));
  for (const f of ordered) {
    const label = isUnknownFace(f) ? unnamedName(f, allFaces) : faceLabel(f, true);
    const key = f.person_id != null ? `p-${f.person_id}` : `f-${f.id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    chips.push(
      <span key={key} className={`namechip ${isUnknownFace(f) ? "unnamed" : "named"}`} style={toneVars(f, allFaces)}>
        {label}
      </span>,
    );
  }

  function onMenu(event) {
    showPhotoMenu(event, { ...photo, rotation: rot });
  }

  const customTags = otherTags(photo.tags);
  const tagOverlay = customTags.length ? <PhotoTagRow tags={customTags} link={!to} /> : null;

  const body = (
    <>
      {to ? (
        <Link className={`labeled ${rot ? `rot-${rot}` : ""}`} to={to} state={toState} onContextMenu={onMenu} style={rot ? rotVars : undefined}>
          {img}
          {tagOverlay}
        </Link>
      ) : (
        <div
          className={`labeled ${onPhotoClick && !selecting ? "zoomable" : ""} ${rot ? `rot-${rot}` : ""} ${selecting ? "selecting" : ""}`}
          onClick={selecting ? undefined : onPhotoClick}
          onContextMenu={onMenu}
          style={rot ? rotVars : undefined}
        >
          {img}
          {tagOverlay}
        </div>
      )}
      {tagsBelow && chips.length ? <div className="name-row">{chips}</div> : null}
    </>
  );

  if (tagsBelow || customTags.length) return <div className="labeled-wrap">{body}</div>;
  return body;
}
