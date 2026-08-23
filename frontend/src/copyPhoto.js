import { readFullscreenLabels } from "./nametag.js";

const COPY_LONG_EDGE = 2560;
const TAG_EDGE = 8;

export function displayedPhotoImage(photo) {
  const id = photo?.id;
  const filename = photo?.filename;
  const full = document.querySelector(".photo-full img");
  if (usableImage(full)) return full;
  const imgs = [...document.querySelectorAll(".labeled img, .stage img")];
  const match = imgs.find((img) => {
    if (!usableImage(img)) return false;
    const src = img.currentSrc || img.src || "";
    if (id != null && src.includes(`/photos/${id}/`)) return true;
    if (filename && img.alt === filename) return true;
    return false;
  });
  return match || imgs.find(usableImage) || null;
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

export function readVisibleNameTags(img) {
  if (!img || typeof img.closest !== "function") return null;
  const host = img.closest(".labeled-rot") || img.closest(".labeled-inner");
  if (!host) return null;
  const imgRect = img.getBoundingClientRect();
  if (!imgRect.width || !imgRect.height) return null;
  const items = [];
  for (const el of host.querySelectorAll(".nametag")) {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const style = window.getComputedStyle(el);
    const nEl = el.querySelector(".nametag-n");
    const nRect = nEl?.getBoundingClientRect();
    const nText = nEl?.textContent?.trim() || "";
    let label = "";
    for (const node of el.childNodes) {
      if (node.nodeType === 3) label += node.textContent;
    }
    label = label.replace(/\s+/g, " ").trim();
    const text = (el.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    items.push({
      x: r.left - imgRect.left,
      y: r.top - imgRect.top,
      w: r.width,
      h: r.height,
      text,
      label: label || text,
      n: nText,
      nX: nRect ? nRect.left - r.left : 0,
      nY: nRect ? nRect.top - r.top : 0,
      nW: nRect ? nRect.width : 0,
      nH: nRect ? nRect.height : 0,
      bg: style.backgroundColor,
      fg: style.color,
      border: style.borderColor,
      fontSize: parseFloat(style.fontSize) || 12,
      fontWeight: style.fontWeight || "600",
      fontFamily: style.fontFamily || "sans-serif",
    });
  }
  if (!items.length) return null;
  return { imgW: imgRect.width, imgH: imgRect.height, items };
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
  const x = ox + tag.x * scale;
  const y = oy + tag.y * scale;
  const w = Math.max(2, tag.w * scale);
  const h = Math.max(2, tag.h * scale);
  const fontSize = Math.max(8, tag.fontSize * scale);
  ctx.save();
  roundRect(ctx, x, y, w, h, h / 2);
  ctx.fillStyle = tag.bg || "rgba(26, 22, 18, 0.88)";
  ctx.fill();
  if (tag.border && tag.border !== "rgba(0, 0, 0, 0)") {
    ctx.strokeStyle = tag.border;
    ctx.lineWidth = Math.max(1, scale);
    ctx.stroke();
  }
  ctx.clip();
  ctx.font = `${tag.fontWeight} ${fontSize}px ${tag.fontFamily}`;
  ctx.textBaseline = "middle";
  if (tag.n && tag.nW > 0) {
    const nx = x + tag.nX * scale;
    const ny = oy + tag.y * scale + tag.nY * scale;
    const nw = tag.nW * scale;
    const nh = tag.nH * scale;
    const cx = nx + nw / 2;
    const cy = ny + nh / 2;
    ctx.fillStyle = "rgba(255, 253, 250, 0.92)";
    ctx.beginPath();
    ctx.arc(cx, cy, Math.max(4, Math.min(nw, nh) / 2), 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#1a1612";
    ctx.textAlign = "center";
    ctx.fillText(tag.n, cx, cy + 0.5);
    ctx.fillStyle = tag.fg || "#fffdfa";
    ctx.textAlign = "left";
    const textX = nx + nw + Math.max(4, 5 * scale);
    ctx.fillText(tag.label, textX, y + h / 2);
  } else {
    ctx.fillStyle = tag.fg || "#fffdfa";
    ctx.textAlign = "center";
    ctx.fillText(tag.text, x + w / 2, y + h / 2);
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
  const pad = tags
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

export async function imageBlobForClipboard(photo) {
  const shown = displayedPhotoImage(photo);
  const labelsOn = readFullscreenLabels();
  const tags = labelsOn && shown ? readVisibleNameTags(shown) : null;
  if (shown) return drawToClipboardCanvas(shown, photo.rotation, tags);
  const url = photo.file_url || photo.thumb_url || `/api/photos/${photo.id}/file`;
  const res = await fetch(url);
  if (!res.ok) throw new Error("Could not read this photo.");
  const blob = await res.blob();
  const bitmap = await createImageBitmap(blob);
  try {
    return await drawToClipboardCanvas(bitmap, photo.rotation, null);
  } finally {
    bitmap.close?.();
  }
}
