import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { FULLSCREEN_LABELS_EVENT, NAMETAG_EVENT, readFullscreenLabels, readNametag } from "../nametag.js";
import { PHOTO_CHANGE_EVENT, showPhotoMenu } from "../photoMenu.js";
import { PhotoTagRow } from "./PhotoTags.jsx";

const FACE_TONES = ["#c45a32", "#1f8a7a", "#d4a017", "#3d7ec9", "#c44d7a", "#4a8f3a", "#7b5ea7", "#e07a2f"];

export function faceTone(face, faces) {
  const mark = faces ? faceMark(face, faces) : null;
  if (mark) return FACE_TONES[(mark - 1) % FACE_TONES.length];
  const n = Math.abs(Math.trunc(Number(face?.id || 0))) || 0;
  return FACE_TONES[n % FACE_TONES.length];
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
      if (boxIou(seed, other) >= 0.72) group.push(other);
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

function pctRect(face, w, h) {
  const padX = Math.max(4, (face.x2 - face.x1) * 0.24);
  const padY = Math.max(6, (face.y2 - face.y1) * 0.32);
  return {
    x1: (100 * (face.x1 - padX)) / w,
    y1: (100 * (face.y1 - padY)) / h,
    x2: (100 * (face.x2 + padX)) / w,
    y2: (100 * (face.y2 + padY)) / h,
  };
}

function bodyRect(faceRect) {
  const fw = Math.max(2, faceRect.x2 - faceRect.x1);
  const fh = Math.max(2, faceRect.y2 - faceRect.y1);
  return {
    x1: faceRect.x1 - fw * 0.18,
    y1: faceRect.y2,
    x2: faceRect.x2 + fw * 0.18,
    y2: Math.min(100, faceRect.y2 + fh * 1.85),
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

function tagRect(left, top, tw, th, place, gap = 1.6) {
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

function outwardPlaces(faceRect, clusterRects, preferred) {
  const own = centerOf(faceRect);
  let cx = 0;
  let cy = 0;
  for (const rect of clusterRects) {
    const c = centerOf(rect);
    cx += c.x;
    cy += c.y;
  }
  cx /= clusterRects.length;
  cy /= clusterRects.length;
  const dx = own.x - cx;
  const dy = own.y - cy;
  const ranked = [
    { place: "right", score: dx },
    { place: "left", score: -dx },
    { place: "below", score: dy },
    { place: "above", score: -dy },
  ].sort((a, b) => b.score - a.score);
  const order = ranked.map((item) => item.place);
  const nearTop = faceRect.y1 < 9;
  const nearBottom = faceRect.y2 > 91;
  const nearLeft = faceRect.x1 < 8;
  const nearRight = faceRect.x2 > 92;
  const filtered = order.filter((place) => {
    if (place === "above" && nearTop) return false;
    if (place === "below" && nearBottom) return false;
    if (place === "left" && nearLeft) return false;
    if (place === "right" && nearRight) return false;
    return true;
  });
  const crowded = clusterRects.length >= 2;
  if (!crowded && preferred && preferred !== "on") {
    return [...new Set([preferred, ...filtered, ...order])];
  }
  const places = [...new Set([...filtered, ...order, "above", "below", "right", "left"])];
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

function scoreTag(rect, tagObstacles, faceRects, bodyRects, ownFace, place, preferred, outward) {
  let score = outOfBounds(rect) * 90;
  for (const other of tagObstacles) {
    const area = overlapArea(rect, other);
    if (area) score += area * 28;
  }
  for (const face of faceRects) {
    const area = overlapArea(rect, face);
    if (!area) continue;
    const own = Math.abs(face.x1 - ownFace.x1) < 0.01 && Math.abs(face.y1 - ownFace.y1) < 0.01;
    score += area * (own ? 70 : 48);
  }
  for (const body of bodyRects) {
    const area = overlapArea(rect, body);
    if (area) score += area * 18;
  }
  const ac = centerOf(ownFace);
  const tc = { x: (rect.x1 + rect.x2) / 2, y: (rect.y1 + rect.y2) / 2 };
  const dist = Math.hypot(tc.x - ac.x, tc.y - ac.y);
  const faceSize = Math.max(ownFace.x2 - ownFace.x1, ownFace.y2 - ownFace.y1);
  score += dist * 0.5;
  if (dist > faceSize * 2.1 + 7) score += (dist - faceSize * 2.1) * 2.4;
  if (place === preferred && outward.length <= 1) score -= 1.6;
  if (place === outward[0]) score -= 2.6;
  if (place === outward[1]) score -= 1.1;
  if (place === "above") score -= 0.45;
  if (place === "on") score += 14;
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

function layoutTags(faces, w, h, preferredPlace, keepUnnamed = false, pins = null) {
  const compact = faces.length >= 5;
  const faceRects = faces.map((face) => pctRect(face, w, h));
  const bodyRects = faceRects.map((rect) => bodyRect(rect));
  const jobs = faces
    .map((face, index) => {
      const named = !isUnknownFace(face);
      const label = named ? faceLabel(face, true) : unnamedName(face, faces);
      const chars = String(label || "").length;
      const tw = Math.min(32, Math.max(7.2, chars * 0.78 + 6.2));
      const th = 4.4;
      return { face, index, named, label, tw, th, compact: compact && !named, faceRect: faceRects[index] };
    })
    .filter((job) => keepUnnamed || !job.compact);
  jobs.sort((a, b) => {
    const ac = centerOf(a.faceRect);
    const bc = centerOf(b.faceRect);
    const aEdge = Math.min(ac.x, 100 - ac.x, ac.y, 100 - ac.y);
    const bEdge = Math.min(bc.x, 100 - bc.x, bc.y, 100 - bc.y);
    return aEdge - bEdge || Number(b.named) - Number(a.named) || ac.x - bc.x;
  });

  const tagObstacles = [];
  const placed = [];
  const pinnedIds = new Set();
  for (const job of jobs) {
    const pin = pinOf(job.face, pins);
    if (!pin) continue;
    const rect = {
      x1: pin.left - job.tw / 2,
      y1: pin.top - job.th / 2,
      x2: pin.left + job.tw / 2,
      y2: pin.top + job.th / 2,
    };
    tagObstacles.push(rect);
    placed.push({ ...job, left: pin.left, top: pin.top, place: "manual", rect });
    pinnedIds.add(job.face.id);
  }
  const standOffs = [0.8, 2.4, 4.4, 7.2, 10.5];
  const slides = [0, -4, 4, -8, 8];
  for (const job of jobs) {
    if (pinnedIds.has(job.face.id)) continue;
    const cluster = clusterAround(job.index, faceRects);
    const outward = outwardPlaces(job.faceRect, cluster, preferredPlace);
    let best = null;
    let bestScore = Infinity;
    for (const side of outward) {
      for (const standoff of standOffs) {
        for (const slide of slides) {
          const anchor = anchorFor(job.faceRect, side, standoff);
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
            preferredPlace,
            outward,
          );
          if (score < bestScore) {
            bestScore = score;
            best = { ...job, left, top, place: side, rect };
          }
        }
      }
    }
    if (best) {
      tagObstacles.push(best.rect);
      placed.push(best);
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
}) {
  const place = useNametagPlacement();
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
  };
  const allFaces = useMemo(() => displayFaces(photo.faces || []), [photo.faces]);
  const faces = useMemo(
    () => overlayFaces(photo.faces || [], { showHidden, hideUnknown }),
    [photo.faces, showHidden, hideUnknown],
  );
  const overlay = overlayTags === undefined ? labelsOn : !!overlayTags;
  const [dragPin, setDragPin] = useState(null);
  const [localPins, setLocalPins] = useState({});
  const drag = useRef(null);
  const skipClick = useRef(false);
  const pick = useRef(null);
  const pickHostRef = useRef(null);
  const [pickRect, setPickRect] = useState(null);
  useEffect(() => {
    setLocalPins({});
    setDragPin(null);
  }, [photo.id]);
  const pins = useMemo(() => {
    const out = {};
    for (const f of faces) {
      if (f.tag_x == null || f.tag_y == null) continue;
      const left = Number(f.tag_x);
      const top = Number(f.tag_y);
      if (Number.isFinite(left) && Number.isFinite(top)) out[f.id] = { left, top };
    }
    Object.assign(out, localPins);
    if (dragPin) out[dragPin.id] = { left: dragPin.left, top: dragPin.top };
    return out;
  }, [faces, localPins, dragPin]);
  const tagGroups = useMemo(
    () => (overlay ? layoutTags(faces, w, h, place, Boolean(showUnnamed || showHidden), pins) : []),
    [overlay, faces, w, h, place, showUnnamed, showHidden, pins],
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
    const box = host?.getBoundingClientRect?.();
    if (!box?.width || !box?.height) return { left: 50, top: 50 };
    return {
      left: Math.min(99, Math.max(1, (100 * (clientX - box.left)) / box.width)),
      top: Math.min(99, Math.max(1, (100 * (clientY - box.top)) / box.height)),
    };
  }

  function onTagPointerDown(event, face, start) {
    if (selecting) return;
    if (!movable) return;
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const host = tagHost(event.currentTarget);
    drag.current = {
      id: face.id,
      face,
      host,
      origin: { left: start.left, top: start.top },
      startX: event.clientX,
      startY: event.clientY,
      moved: false,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function onTagPointerMove(event) {
    const d = drag.current;
    if (!d || d.id == null) return;
    const dx = event.clientX - d.startX;
    const dy = event.clientY - d.startY;
    if (!d.moved && Math.hypot(dx, dy) < 4) return;
    d.moved = true;
    const next = clientToPct(d.host, event.clientX, event.clientY);
    d.pin = next;
    setDragPin({ id: d.id, left: next.left, top: next.top });
  }

  function onTagPointerUp(event) {
    const d = drag.current;
    if (!d) return;
    const moved = d.moved;
    const face = d.face;
    const pin = d.pin;
    drag.current = null;
    setDragPin(null);
    if (!moved || !pin) return;
    skipClick.current = true;
    event.preventDefault();
    event.stopPropagation();
    setLocalPins((cur) => ({ ...cur, [face.id]: { left: pin.left, top: pin.top } }));
    onTagMove?.(face, pin);
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
    return node?.closest(".labeled-rot, .labeled-inner") || node;
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
              const label = unknown ? unnamedName(f, faces) : item.label || faceLabel(f, true);
              const n = unknown ? null : faceMark(f, faces);
              const dragging = dragPin?.id === f.id;
              return (
                <button
                  key={`tag-${f.id}`}
                  type="button"
                  className={`nametag place-${item.place} ${isUnknownFace(f) ? "unnamed" : "named"} ${item.compact ? "compact" : ""} ${f.id === activeId ? "active" : ""} ${movable ? "movable" : ""} ${dragging ? "dragging" : ""}`}
                  style={{ left: `${item.left}%`, top: `${item.top}%`, ...toneVars(f, faces) }}
                  data-face-id={f.id}
                  aria-label={
                    (n ? `${n}. ${label || "Face"}` : label || "Face") + (f.comment ? `. ${f.comment}` : "")
                  }
                  title={f.comment || undefined}
                  onPointerDown={(e) => onTagPointerDown(e, f, { left: item.left, top: item.top })}
                  onPointerMove={onTagPointerMove}
                  onPointerUp={onTagPointerUp}
                  onPointerCancel={onTagPointerUp}
                  onDoubleClick={(e) => onTagDoubleClick(e, f)}
                  onClick={(e) => onTagClick(e, f)}
                >
                  {n ? <span className="nametag-n">{n}</span> : null}
                  {label}
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
      className={`labeled-inner place-${place} ${fit ? "fit" : ""} ${rot ? `rot-${rot}` : ""}`}
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

  const customTags = (photo.tags || []).filter(Boolean);
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
