import { readFullscreenLabels, readLabelSize, readLabelStyle } from "./nametag.js";

const FACE_TONES = ["#c45a32", "#1f8a7a", "#d4a017", "#3d7ec9", "#c44d7a", "#4a8f3a", "#7b5ea7", "#e07a2f"];
const CSS_FONT = { small: 10, medium: 11, large: 13 };
const COPY_LONG_EDGE = 2560;
const TAG_EDGE = 8;
const DISPLAY_REF = 960;

function visibleFaces(faces) {
  return [...(faces || [])]
    .filter((f) => f && f.assigned_how !== "junk")
    .sort((a, b) => (Number(a.x1) || 0) - (Number(b.x1) || 0) || (Number(a.id) || 0) - (Number(b.id) || 0));
}

function namedLabel(face) {
  const name = String(face?.person_name || "").trim();
  if (name && name !== "unnamed" && !name.startsWith("Unknown name of person")) return name;
  if (face?.person_id) return name || "unnamed";
  return "";
}

function matchesPhoto(img, photo) {
  if (!img || !photo) return false;
  const src = img.currentSrc || img.src || "";
  if (photo.id != null && src.includes(`/photos/${photo.id}/`)) return true;
  const filename = photo.filename;
  return Boolean(filename && img.alt === filename);
}

export function displayedPhotoImage(photo) {
  const ctx = copySource(photo);
  return ctx.img;
}

function copySource(photo) {
  const full = document.querySelector(".photo-full");
  if (full) {
    const img = [...full.querySelectorAll("img")].find(usableImage);
    const host = full.querySelector(".labeled-rot, .labeled-inner");
    if (img) return { img, host: host || img.closest(".labeled-inner") };
  }
  const imgs = [...document.querySelectorAll(".labeled img, .stage img")].filter(usableImage);
  const img = imgs.find((el) => matchesPhoto(el, photo)) || imgs[0] || null;
  const host = img?.closest(".labeled-rot") || img?.closest(".labeled-inner") || null;
  return { img, host };
}

export function copyChipFontPx(imgW, sizeId = readLabelSize()) {
  const css = CSS_FONT[sizeId] || CSS_FONT.small;
  const w = Number(imgW) || DISPLAY_REF;
  return Math.max(8, Math.min(34, (css * w) / DISPLAY_REF));
}

function usableImage(img) {
  return Boolean(img?.complete && (img.naturalWidth || img.width));
}

export function tagBoundsPad(imgW, imgH, rects, margin = 0) {
  let minX = 0;
  let minY = 0;
  let maxX = imgW;
  let maxY = imgH;
  for (const r of rects || []) {
    minX = Math.min(minX, (Number(r.x) || 0) - margin);
    minY = Math.min(minY, (Number(r.y) || 0) - margin);
    maxX = Math.max(maxX, (Number(r.x) || 0) + (Number(r.w) || 0) + margin);
    maxY = Math.max(maxY, (Number(r.y) || 0) + (Number(r.h) || 0) + margin);
  }
  return {
    left: Math.max(0, Math.ceil(-minX)),
    top: Math.max(0, Math.ceil(-minY)),
    right: Math.max(0, Math.ceil(maxX - imgW)),
    bottom: Math.max(0, Math.ceil(maxY - imgH)),
  };
}

export function imageContentRect(img) {
  const br = img.getBoundingClientRect();
  if (!br.width || !br.height) return null;
  const nw = img.naturalWidth || 0;
  const nh = img.naturalHeight || 0;
  let left = br.left;
  let top = br.top;
  let width = br.width;
  let height = br.height;
  let fit = "fill";
  try {
    fit = String(img.ownerDocument?.defaultView?.getComputedStyle?.(img)?.objectFit || "fill").toLowerCase();
  } catch {
    fit = "fill";
  }
  if (nw && nh && (fit === "contain" || fit === "scale-down")) {
    const s = Math.min(br.width / nw, br.height / nh);
    width = nw * s;
    height = nh * s;
    left = br.left + (br.width - width) / 2;
    top = br.top + (br.height - height) / 2;
  }
  return { left, top, width, height };
}

export function visualRectToLayout(r, box) {
  const sx = box.sx || 1;
  const sy = box.sy || 1;
  return {
    x: (r.left - box.rect.left) / sx,
    y: (r.top - box.rect.top) / sy,
    w: r.width / sx,
    h: r.height / sy,
  };
}

function isTransparentColor(value) {
  const c = String(value || "").trim().toLowerCase();
  if (!c || c === "transparent") return true;
  const m = c.match(/rgba?\(([^)]+)\)/);
  if (!m) return false;
  const parts = m[1].split(",").map((part) => parseFloat(part.trim()));
  return parts.length === 4 && parts[3] === 0;
}

function median(values) {
  const list = values.filter((n) => Number.isFinite(n) && n > 0).sort((a, b) => a - b);
  if (!list.length) return 0;
  return list[Math.floor(list.length / 2)];
}

export function nameTagChipSize(tag, scale = 1, measure) {
  const compact = Boolean(tag.compact) || Boolean(tag.n && !String(tag.label || "").trim());
  const fontSize = Math.max(8, (Number(tag.fontSize) || 12) * scale);
  const padY = Math.max(2, fontSize * 0.16);
  const padX = Math.max(5, fontSize * 0.55);
  const nSize = tag.n ? fontSize * 1.15 : 0;
  const gap = tag.n && !compact ? Math.max(4, fontSize * 0.38) : 0;
  const text = compact ? "" : String(tag.n ? tag.label : tag.text || tag.label || "");
  const textW = measure ? measure(text) : text.length * fontSize * 0.56;
  const h = Math.max(nSize, fontSize) + padY * 2;
  const rawW = compact ? h : padX + nSize + gap + textW + padX;
  const maxW = Number(tag.maxW) > 0 ? Number(tag.maxW) * scale : rawW;
  return { w: Math.min(rawW, maxW), h, fontSize, padX, padY, nSize, gap, compact, text };
}

export function fitCopiedTags(tags) {
  if (!tags?.items?.length) return tags;
  const namedFont = median(tags.items.filter((tag) => !tag.compact).map((tag) => Number(tag.fontSize)));
  const compactFont = median(tags.items.filter((tag) => tag.compact).map((tag) => Number(tag.fontSize)));
  const maxW = Math.max(48, (Number(tags.imgW) || 0) * 0.92);
  const items = tags.items.map((tag) => {
    const next = { ...tag, maxW: maxW || tag.maxW };
    if (next.compact && compactFont) next.fontSize = compactFont;
    else if (!next.compact && namedFont) next.fontSize = namedFont;
    const chip = nameTagChipSize(next, 1);
    const cx = (Number(next.x) || 0) + (Number(next.w) || chip.w) / 2;
    const cy = (Number(next.y) || 0) + (Number(next.h) || chip.h) / 2;
    next.w = chip.w;
    next.h = chip.h;
    next.x = cx - chip.w / 2;
    next.y = cy - chip.h / 2;
    next.compact = chip.compact;
    next.nW = chip.nSize;
    next.nH = chip.nSize;
    next.nX = chip.padX;
    next.nY = (chip.h - chip.nSize) / 2;
    return next;
  });
  return { ...tags, items };
}

function nametagHost(img) {
  if (!img || typeof img.closest !== "function") return null;
  const full = img.closest(".photo-full") || document.querySelector(".photo-full");
  if (full && (full.contains(img) || document.querySelector(".photo-full img") === img)) {
    return full.querySelector(".labeled-rot, .labeled-inner") || full;
  }
  return img.closest(".labeled-rot") || img.closest(".labeled-inner") || img.closest(".labeled");
}

export function readVisibleNameTags(img) {
  if (!img) return null;
  const host = nametagHost(img);
  const photoBox = imageContentRect(img);
  if (!host || !photoBox?.width || !photoBox.height) return null;
  const layoutW = img.offsetWidth || photoBox.width;
  const zoom = layoutW ? photoBox.width / layoutW : 1;
  const chip = document.documentElement?.getAttribute?.("data-label-style") || readLabelStyle();
  const items = [];
  for (const el of host.querySelectorAll(".nametag")) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const style = window.getComputedStyle(el);
    const nEl = el.querySelector(".nametag-n");
    const nText = nEl?.textContent?.trim() || "";
    const nRect = nEl?.getBoundingClientRect();
    let label = "";
    for (const node of el.childNodes) {
      if (node.nodeType === 3) label += node.textContent;
    }
    label = label.replace(/\s+/g, " ").trim();
    const text = (el.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    items.push({
      x: r.left - photoBox.left,
      y: r.top - photoBox.top,
      w: r.width,
      h: r.height,
      text,
      label: label || (nText && nText !== text ? "" : text),
      n: nText,
      compact: el.classList.contains("compact") || Boolean(nText && !label),
      nX: nRect ? nRect.left - r.left : 0,
      nY: nRect ? nRect.top - r.top : 0,
      nW: nRect ? nRect.width : 0,
      nH: nRect ? nRect.height : 0,
      bg: style.backgroundColor,
      fg: style.color,
      border: style.borderColor,
      fontSize: (parseFloat(style.fontSize) || 12) * zoom,
      fontWeight: style.fontWeight || "600",
      fontFamily: style.fontFamily || "sans-serif",
      chip,
    });
  }
  if (!items.length) return null;
  return { imgW: photoBox.width, imgH: photoBox.height, items };
}

function roundRect(ctx, x, y, w, h, radius) {
  const r = Math.max(0, Math.min(radius, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

function drawNameTag(ctx, tag, scale, ox, oy) {
  const x = ox + Number(tag.x) * scale;
  const y = oy + Number(tag.y) * scale;
  const w = Math.max(2, Number(tag.w) * scale);
  const h = Math.max(2, Number(tag.h) * scale);
  const fontSize = Math.max(1, Number(tag.fontSize) * scale);
  const shadow = tag.chip === "shadow" || isTransparentColor(tag.bg);
  const outline = tag.chip === "outline";
  ctx.save();
  ctx.font = `${tag.fontWeight || 600} ${fontSize}px ${tag.fontFamily || "sans-serif"}`;
  ctx.textBaseline = "middle";
  if (!shadow) {
    roundRect(ctx, x, y, w, h, h / 2);
    ctx.fillStyle = tag.bg || "rgba(26, 22, 18, 0.88)";
    ctx.fill();
    if (outline || (tag.border && !isTransparentColor(tag.border))) {
      ctx.strokeStyle = tag.border && !isTransparentColor(tag.border) ? tag.border : "rgba(255, 253, 250, 0.7)";
      ctx.lineWidth = Math.max(1, scale);
      ctx.stroke();
    }
  }
  const midY = y + h / 2;
  const fg = tag.fg || "#fffdfa";
  function paintText(align, tx, content) {
    if (!content) return;
    ctx.textAlign = align;
    if (shadow) {
      ctx.lineJoin = "round";
      ctx.miterLimit = 2;
      ctx.lineWidth = Math.max(2, fontSize * 0.22);
      ctx.strokeStyle = "rgba(26, 22, 18, 0.92)";
      ctx.strokeText(content, tx, midY);
    }
    ctx.fillStyle = shadow ? "#fffdfa" : fg;
    ctx.fillText(content, tx, midY);
  }
  if (tag.n && Number(tag.nW) > 0) {
    const nw = Number(tag.nW) * scale;
    const nh = Number(tag.nH) * scale;
    const nx = x + Number(tag.nX) * scale;
    const ny = y + Number(tag.nY) * scale;
    const cx = nx + nw / 2;
    const cy = ny + nh / 2;
    if (!shadow) {
      ctx.fillStyle = "rgba(255, 253, 250, 0.92)";
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(3, Math.min(nw, nh) / 2), 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.fillStyle = "#1a1612";
    ctx.textAlign = "center";
    ctx.fillText(tag.n, cx, cy + 0.5);
    if (!tag.compact) paintText("left", nx + nw + Math.max(3, 4 * scale), tag.label);
  } else {
    paintText("center", x + w / 2, tag.text || tag.label || "");
  }
  ctx.restore();
}

export function drawToClipboardCanvas(source, rotation = 0, tags = null) {
  const srcW = source.naturalWidth || source.width;
  const srcH = source.naturalHeight || source.height;
  if (!srcW || !srcH) throw new Error("Could not read this photo.");
  const rot = (((Number(rotation) || 0) % 360) + 360) % 360;
  const swap = rot === 90 || rot === 270;
  const long = Math.max(srcW, srcH);
  const scale = long > COPY_LONG_EDGE ? COPY_LONG_EDGE / long : 1;
  const w = Math.max(1, Math.round(srcW * scale));
  const h = Math.max(1, Math.round(srcH * scale));
  const imgW = swap ? h : w;
  const imgH = swap ? w : h;
  const displayW = tags?.imgW || imgW;
  const tagScale = imgW / displayW;
  const pad = tags?.items?.length
    ? tagBoundsPad(displayW, tags.imgH || displayW, tags.items, TAG_EDGE)
    : { left: 0, top: 0, right: 0, bottom: 0 };
  const left = Math.round(pad.left * tagScale);
  const top = Math.round(pad.top * tagScale);
  const right = Math.round(pad.right * tagScale);
  const bottom = Math.round(pad.bottom * tagScale);
  const canvas = document.createElement("canvas");
  canvas.width = imgW + left + right;
  canvas.height = imgH + top + bottom;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.translate(left + imgW / 2, top + imgH / 2);
  if (rot) ctx.rotate((rot * Math.PI) / 180);
  ctx.drawImage(source, -w / 2, -h / 2, w, h);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (tags?.items?.length) {
    for (const tag of tags.items) drawNameTag(ctx, tag, tagScale, left, top);
  }
  return canvasToPng(canvas);
}

function canvasToPng(canvas) {
  return new Promise((resolve, reject) => {
    canvas.toBlob((out) => {
      if (!out) reject(new Error("Could not copy this photo."));
      else resolve(out);
    }, "image/png");
  });
}

export function tagsFromFaces(photo, imgW, imgH) {
  const w = Number(imgW) || Number(photo?.width) || 0;
  const h = Number(imgH) || Number(photo?.height) || 0;
  if (!w || !h) return null;
  const faces = visibleFaces(photo?.faces || []).filter((face) => namedLabel(face));
  const fontSize = copyChipFontPx(w);
  const chip = readLabelStyle();
  const items = [];
  faces.forEach((face, i) => {
    const label = namedLabel(face);
    const n = String(i + 1);
    const x1 = Number(face.x1) || 0;
    const y1 = Number(face.y1) || 0;
    const x2 = Number(face.x2) || 0;
    const y2 = Number(face.y2) || 0;
    const leftPct = face.tag_x != null && Number.isFinite(Number(face.tag_x)) ? Number(face.tag_x) : ((x1 + x2) / 2 / w) * 100;
    const topPct =
      face.tag_y != null && Number.isFinite(Number(face.tag_y))
        ? Number(face.tag_y)
        : Math.max(2.5, (y1 / h) * 100 - 4);
    const draft = {
      x: (leftPct / 100) * w,
      y: (topPct / 100) * h,
      w: 0,
      h: 0,
      text: `${n} ${label}`,
      label,
      n,
      compact: false,
      bg: FACE_TONES[i % FACE_TONES.length],
      fg: "#fffdfa",
      border: "transparent",
      fontSize,
      fontWeight: "600",
      fontFamily: "system-ui, sans-serif",
      chip,
    };
    const size = nameTagChipSize(draft, 1);
    draft.w = size.w;
    draft.h = size.h;
    draft.x -= size.w / 2;
    draft.y -= size.h / 2;
    items.push(draft);
  });
  if (!items.length) return null;
  return fitCopiedTags({ imgW: w, imgH: h, items });
}

export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || "photo.jpg";
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function imageBlobForClipboard(photo, { labels } = {}) {
  const source = copySource(photo);
  const shown = source.img;
  const wantLabels = labels === undefined ? readFullscreenLabels() : Boolean(labels);
  let tags = wantLabels && shown ? readVisibleNameTags(shown) : null;
  if (wantLabels && !tags) {
    const w = shown?.naturalWidth || Number(photo?.width) || 0;
    const h = shown?.naturalHeight || Number(photo?.height) || 0;
    tags = tagsFromFaces(photo, w, h);
  }
  if (shown) return drawToClipboardCanvas(shown, photo.rotation, wantLabels ? tags : null);
  const url = photo.file_url || photo.view_url || photo.thumb_url || `/api/photos/${photo.id}/file`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Could not read this photo.");
  const blob = await res.blob();
  const bitmap = await createImageBitmap(blob);
  try {
    const drawn = wantLabels ? tagsFromFaces(photo, bitmap.width, bitmap.height) : null;
    return await drawToClipboardCanvas(bitmap, photo.rotation, drawn);
  } finally {
    bitmap.close?.();
  }
}

export async function downloadPhotoFile(photo, { labels = false } = {}) {
  const base = String(photo?.filename || `photo-${photo?.id || "file"}`).replace(/[/\\]+/g, "_");
  if (!labels) {
    const url = photo.file_url || photo.view_url || `/api/photos/${photo.id}/file`;
    const res = await fetch(url);
    if (!res.ok) throw new Error("Could not read this photo.");
    const blob = await res.blob();
    const name = photo.file_url ? base : base.replace(/\.[^.]+$/, "") + ".jpg";
    saveBlob(blob, name);
    return;
  }
  const png = await imageBlobForClipboard(photo, { labels: true });
  const stem = base.replace(/\.[^.]+$/, "");
  saveBlob(png, `${stem}-labeled.png`);
}
