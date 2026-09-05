import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import {
  CARD_H,
  CARD_W,
  clampZoom,
  layoutDoubleAncestorChart,
  layoutFamilyTree,
  layoutFanChart,
  FAN_DEPTH,
  FAN_MAX_DEPTH,
  treePersonIdForCatalog,
  viewOnFocus,
  wheelZoomFactor,
} from "../familyChart.js";
import { paletteById, readPalette } from "../chartPalette.js";
import { expandShortNames, nameVariants, queryMatchesName } from "../nameSuggest.js";
import { enterBrowserFullscreen, exitBrowserFullscreen } from "../play.js";
import { tip } from "../tip.js";

// Married name first, birth surname as née: "Joan Margaret Evans (née Henbrey)".
export function cardName(node) {
  const name = String(node?.name || "").trim();
  const married = String(node?.married_surname || "").trim();
  if (!married) return name;
  const birth = String(node?.surname || "").trim();
  if (!birth || /^\(?(unknown|mother|father|\?+)\)?$/i.test(birth)) {
    const words0 = name.split(/\s+/).filter(Boolean);
    const stripped = birth && words0.length > 1 && words0[words0.length - 1].toLowerCase() === birth.toLowerCase() ? words0.slice(0, -1) : words0;
    return `${stripped.join(" ")} ${married}`.trim();
  }
  const words = name.split(/\s+/).filter(Boolean);
  const endsWithBirth = birth && words.length > 1 && words[words.length - 1].toLowerCase() === birth.toLowerCase();
  if (!endsWithBirth) return `${name} (${married})`;
  return `${words.slice(0, -1).join(" ")} ${married} (née ${birth})`;
}

function PersonGlyph() {
  return (
    <svg viewBox="0 0 64 64" aria-hidden="true" className="ged-glyph">
      <circle cx="32" cy="24" r="12" />
      <path d="M12 56c2-12 10-18 20-18s18 6 20 18z" />
    </svg>
  );
}

function splitLines(text, max) {
  const words = String(text || "").split(/\s+/).filter(Boolean);
  const lines = [];
  let cur = "";
  for (const w of words) {
    if (cur && (cur + " " + w).length > max) {
      lines.push(cur);
      cur = w;
    } else cur = cur ? `${cur} ${w}` : w;
  }
  if (cur) lines.push(cur);
  return lines.slice(0, 3);
}

// Rough text width for fitting: bold sans at this size.
function textWidth(text, size) {
  return String(text || "").length * size * 0.56;
}

// Split into at most `maxLines` lines that each fit `width` at `size`,
// shrinking the font (down to `minSize`) and finally trimming with an ellipsis.
function fitLines(text, { width, size, minSize, maxLines }) {
  let fs = size;
  while (fs >= minSize) {
    const maxChars = Math.max(3, Math.floor(width / (fs * 0.56)));
    const lines = splitLines(text, maxChars);
    if (lines.length <= maxLines && lines.every((l) => textWidth(l, fs) <= width)) return { lines, size: fs };
    fs -= 0.5;
  }
  fs = minSize;
  const maxChars = Math.max(3, Math.floor(width / (fs * 0.56)));
  const lines = splitLines(text, maxChars).slice(0, maxLines);
  const last = lines[lines.length - 1] || "";
  if (lines.length === maxLines && textWidth(last, fs) > width) lines[lines.length - 1] = `${last.slice(0, Math.max(1, maxChars - 1))}…`;
  return { lines, size: fs };
}

function fanArc(cx, cy, r, a0, a1) {
  const p1 = { x: cx + r * Math.cos(a1), y: cy - r * Math.sin(a1) };
  const p2 = { x: cx + r * Math.cos(a0), y: cy - r * Math.sin(a0) };
  return `M ${p1.x.toFixed(2)} ${p1.y.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > Math.PI ? 1 : 0} 1 ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
}

function FanSegment({ node, onOpen }) {
  const deg = (node.mid * 180) / Math.PI;
  const rMid = (node.r0 + node.r1) / 2;
  const span = node.a1 - node.a0;
  const name = cardName(node);
  const years = node.lifespan || "";
  const photo = node.cover_url && node.g <= 3;
  let textEl;
  let photoEl = null;
  if (node.focus) {
    // Portrait high in the half disc, text block fitted between it and the base.
    const pr = 22;
    const px = node.cx;
    const py = node.cy - 80;
    photoEl = (
      <>
        <clipPath id={`fan-clip-${node.id}`}>
          <circle cx={px} cy={py} r={pr} />
        </clipPath>
        {node.cover_url ? (
          <image href={node.cover_url} x={px - pr} y={py - pr} width={pr * 2} height={pr * 2} preserveAspectRatio="xMidYMid slice" clipPath={`url(#fan-clip-${node.id})`} />
        ) : (
          <circle cx={px} cy={py} r={pr} className="ged-fan-glyph" />
        )}
      </>
    );
    const top = py + pr + 6;
    const bottom = node.cy - 8;
    let size = 15;
    let fit = fitLines(name, { width: 186, size, minSize: 9, maxLines: 2 });
    const blockHeight = (fs, n) => n * fs * 1.15 + (years ? fs * 0.85 * 1.2 : 0);
    while (blockHeight(fit.size, fit.lines.length) > bottom - top && fit.size > 9) {
      size = fit.size - 0.5;
      fit = fitLines(name, { width: 186, size, minSize: 9, maxLines: 2 });
    }
    const first = top + fit.size;
    textEl = (
      <text x={node.cx} y={first} textAnchor="middle" fontSize={fit.size} fontWeight={700}>
        {fit.lines.map((line, i) => (
          <tspan key={i} x={node.cx} dy={i ? fit.size * 1.15 : 0}>{line}</tspan>
        ))}
        {years ? <tspan x={node.cx} dy={fit.size * 1.15} fontWeight={500} fontSize={fit.size * 0.85}>{years}</tspan> : null}
      </text>
    );
  } else if (!node.radial) {
    // Inner rings: text runs along the arc, where the whole arc length is
    // available, on its own concentric path per line.
    const pad = 0.05;
    const a0 = node.a0 + pad * span;
    const a1 = node.a1 - pad * span;
    const photoRoom = photo ? (node.g === 1 ? 52 : 36) : 0;
    const arcLen = rMid * (a1 - a0) - photoRoom;
    const fit = fitLines(name, { width: arcLen, size: node.size, minSize: 8, maxLines: 2 });
    const lineH = fit.size * 1.18;
    const rows = [...fit.lines.map((l) => ({ text: l, bold: true, size: fit.size }))];
    if (years) rows.push({ text: years, bold: false, size: Math.max(7, fit.size * 0.82) });
    const total = rows.reduce((s, r, i) => s + (i ? lineH : 0), 0);
    // Rows read top to bottom, so the first line sits on the outer arc.
    let r = rMid + total / 2 - (photo ? 0 : 0);
    const paths = [];
    const texts = [];
    rows.forEach((row, i) => {
      const id = `fan-arc-${node.id}-${i}`;
      paths.push(<path key={id} id={id} d={fanArc(node.cx, node.cy, r, a0, a1)} fill="none" />);
      texts.push(
        <text key={`t-${id}`} fontSize={row.size} fontWeight={row.bold ? 700 : 500}>
          <textPath href={`#${id}`} startOffset="50%" textAnchor="middle">
            {row.text}
          </textPath>
        </text>,
      );
      r -= lineH;
    });
    textEl = (
      <>
        <defs>{paths}</defs>
        {texts}
      </>
    );
    if (photo) {
      const pr = node.g === 1 ? 24 : 16;
      const pa = node.a1 - (pad + 0.09) * span;
      const rp = node.r0 + 30;
      const pp = { x: node.cx + rp * Math.cos(pa), y: node.cy - rp * Math.sin(pa) };
      photoEl = (
        <>
          <clipPath id={`fan-clip-${node.id}`}>
            <circle cx={pp.x} cy={pp.y} r={pr} />
          </clipPath>
          <image href={node.cover_url} x={pp.x - pr} y={pp.y - pr} width={pr * 2} height={pr * 2} preserveAspectRatio="xMidYMid slice" clipPath={`url(#fan-clip-${node.id})`} />
          <circle cx={pp.x} cy={pp.y} r={pr} className="ged-fan-ring" />
        </>
      );
    }
  } else {
    // Outer rings: radial text, reading outward on the right and inward on the
    // left. Width is the ring depth; height is the arc at the inner radius.
    const width = node.r1 - node.r0 - 14;
    const height = node.r0 * span - 3;
    let fit = fitLines(name, { width, size: node.size, minSize: 6, maxLines: 2 });
    let rows = [...fit.lines];
    let lineH = fit.size * 1.12;
    if (years && (rows.length + 1) * lineH <= height) rows.push(years);
    while (rows.length > 1 && rows.length * lineH > height) {
      if (rows[rows.length - 1] === years) rows.pop();
      else {
        fit = fitLines(name, { width, size: fit.size, minSize: 6, maxLines: 1 });
        rows = [...fit.lines];
        lineH = fit.size * 1.12;
        break;
      }
    }
    const r = node.left ? node.r1 - 7 : node.r0 + 7;
    const p = { x: node.cx + r * Math.cos(node.mid), y: node.cy - r * Math.sin(node.mid) };
    const rot = node.left ? 180 - deg : -deg;
    textEl = (
      <text
        transform={`translate(${p.x.toFixed(1)} ${p.y.toFixed(1)}) rotate(${rot.toFixed(1)})`}
        textAnchor="start"
        fontSize={fit.size}
        fontWeight={700}
        y={-((rows.length - 1) * lineH) / 2 + fit.size * 0.35}
      >
        {rows.map((line, i) => (
          <tspan key={i} x={0} dy={i ? lineH : 0} fontWeight={line === years ? 500 : 700} fontSize={line === years ? fit.size * 0.9 : fit.size}>
            {line}
          </tspan>
        ))}
      </text>
    );
  }
  return (
    <g
      className={`ged-fan-seg${node.focus ? " focus" : ""}`}
      data-person={node.id}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <title>{name}{years ? ` · ${years}` : ""}</title>
      <path className="seg" d={node.path} style={node.fill ? { fill: node.fill } : undefined} />
      {node.band ? <path className="band" d={node.band} style={{ stroke: node.color }} /> : null}
      {photoEl}
      {textEl}
    </g>
  );
}

function CardFact({ label, event, text }) {
  const date = String(event?.date || "").trim();
  const place = String(event?.place || "").trim();
  const value = text || date;
  if (!value && !place) return null;
  return (
    <span className="ged-fact">
      <em>{label}</em>
      {value ? <span>{value}</span> : null}
      {place ? <small title={place}>{place}</small> : null}
    </span>
  );
}

function scorePerson(person, query) {
  let best = 0;
  expandShortNames(query).forEach((needle, i) => {
    const s = scoreOnePerson(person, needle);
    best = Math.max(best, i ? s - 3 : s);
  });
  // Same score, prefer the person who has photos in the catalog.
  return best > 0 && person?.catalog_id ? best + 2 : best;
}

function scoreOnePerson(person, needle) {
  const name = String(person?.name || "").toLowerCase();
  const surname = String(person?.surname || "").toLowerCase();
  const married = String(person?.married_surname || "").toLowerCase();
  const nick = String(person?.nickname || "").toLowerCase();
  // Tree names carry the birth surname; a wife is also known by her married one.
  const variants = nameVariants(name, married).map((v) => v.toLowerCase());
  const hay = `${name} ${surname} ${married} ${nick}`.replace(/\s+/g, " ").trim();
  if (!queryMatchesName(needle, hay) && !variants.some((v) => queryMatchesName(needle, v))) return 0;
  const tokens = needle.split(/\s+/).filter(Boolean);
  const words = `${name} ${married} ${nick}`.split(/\s+/).filter(Boolean);
  if (name === needle || nick === needle || variants.includes(needle)) return 100;
  if (name.startsWith(needle) || nick.startsWith(needle) || variants.some((v) => v.startsWith(needle))) return 90;
  if (surname.startsWith(needle) || (married && married.startsWith(needle))) return 85;
  if (tokens.every((token) => words.some((word) => word.startsWith(token)))) return 80;
  if (words.some((word) => word.startsWith(needle))) return 70;
  return 55;
}

function catalogHitId(id) {
  return `catalog:${id}`;
}

function catalogPersonId(id) {
  const text = String(id || "");
  return text.startsWith("catalog:") ? Number(text.slice(8)) : 0;
}

function NameSearch({
  q,
  setQ,
  suggestions,
  pick,
  setPick,
  suggest,
  setSuggest,
  onJump,
  searchRef,
}) {
  return (
    <form
      className="ged-search"
      onSubmit={(e) => {
        e.preventDefault();
        const hit = suggestions[pick] || suggestions[0];
        if (hit) onJump(hit.id);
      }}
    >
      <input
        ref={searchRef}
        type="search"
        value={q}
        placeholder="Search by name"
        aria-label="Search this tree or named people"
        autoComplete="off"
        onChange={(e) => {
          setQ(e.target.value);
          setSuggest(true);
        }}
        onFocus={() => setSuggest(true)}
        onBlur={() => window.setTimeout(() => setSuggest(false), 120)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setSuggest(true);
            setPick((cur) => Math.min(cur + 1, Math.max(0, suggestions.length - 1)));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setPick((cur) => Math.max(0, cur - 1));
          } else if (e.key === "Escape") {
            if (suggest) {
              e.preventDefault();
              e.stopPropagation();
              setSuggest(false);
            }
          }
        }}
        {...tip("Type a name, then Enter. People in this tree open here. Named people who are only in photos open in Faces in DB View.")}
      />
      {suggest && suggestions.length ? (
        <ul className="ged-suggest" role="listbox">
          {suggestions.map((item, i) => (
            <li key={item.id}>
              <button
                type="button"
                className={i === pick ? "active" : ""}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => onJump(item.id)}
              >
                <span>{cardName(item)}</span>
                {item.lifespan || item.source === "catalog" ? (
                  <span className="hint">{item.lifespan || "In photos"}</span>
                ) : null}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </form>
  );
}

// Which person the entire-tree view last opened on. Switching person while the
// whole file is shown centres on them; switching into the view fits everything.
let lastEntireFocus = null;

function FamilyChart({
  chart,
  onOpen,
  full,
  onToggleFull,
  entire,
  mode = "one",
  onSetMode,
  fanDepth = FAN_DEPTH,
  onFanDepth,
}) {
  const double = mode === "double";
  const fan = mode === "fan";
  const layout = useMemo(
    () =>
      fan
        ? layoutFanChart(chart, { depth: fanDepth, palette: paletteById(readPalette()) })
        : double
          ? layoutDoubleAncestorChart(chart)
          : layoutFamilyTree(chart),
    [chart, double, fan, fanDepth],
  );
  const fanRings = fan ? layout.nodes.reduce((m, n) => Math.max(m, n.g || 0), 0) : 0;
  // Ancestor views with nothing above the person: say why, so a lone card is
  // not mistaken for a broken chart.
  const noAncestors =
    (fan || double) &&
    !layout.nodes.some((n) => !n.focus && n.branch !== "sibling" && (n.g == null || n.g > 0));
  const focusName = cardName(layout.nodes.find((n) => n.focus));
  const stageRef = useRef(null);
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef(null);
  // Entire tree: allow zooming out far enough to see all of it.
  const minZoomRef = useRef(0.12);
  const movedRef = useRef(false);
  const openRef = useRef(onOpen);
  openRef.current = onOpen;
  // A press that turns into a drag must not also count as a click.
  const tapRef = useRef(null);
  function openFromClick(id) {
    if (movedRef.current) return;
    openRef.current(id);
  }
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);

  function applyZoom(next, origin) {
    const z = clampZoom(next, minZoomRef.current);
    const prev = zoomRef.current;
    const p = panRef.current;
    if (origin && prev > 0) {
      const wx = (origin.x - p.x) / prev;
      const wy = (origin.y - p.y) / prev;
      const moved = { x: origin.x - wx * z, y: origin.y - wy * z };
      panRef.current = moved;
      setPan(moved);
    }
    zoomRef.current = z;
    setZoom(z);
  }

  function applyView(next) {
    zoomRef.current = next.zoom;
    panRef.current = { x: next.x, y: next.y };
    setZoom(next.zoom);
    setPan({ x: next.x, y: next.y });
  }

  function centerOnFocus() {
    const el = stageRef.current;
    if (!el || el.clientWidth < 80 || el.clientHeight < 80) return;
    applyView(viewOnFocus(layout, el.clientWidth, el.clientHeight));
  }

  function fit() {
    const el = stageRef.current;
    if (!el || !layout.width || !layout.height) return;
    if (el.clientWidth < 80 || el.clientHeight < 80) return;
    const pad = 36;
    const sx = (el.clientWidth - pad * 2) / layout.width;
    const sy = (el.clientHeight - pad * 2) / layout.height;
    minZoomRef.current = Math.min(0.12, Math.min(sx, sy));
    const z = clampZoom(Math.min(sx, sy, 1.15), minZoomRef.current);
    applyView({
      zoom: z,
      x: (el.clientWidth - layout.width * z) / 2,
      y: (el.clientHeight - layout.height * z) / 2,
    });
  }

  // Entire tree: the file is far wider than tall, so a true fit is a thin
  // line. Fit the height, keep cards legible, and centre on the person.
  function fitTall() {
    const el = stageRef.current;
    if (!el || !layout.width || !layout.height) return;
    if (el.clientWidth < 80 || el.clientHeight < 80) return;
    const pad = 36;
    const sx = (el.clientWidth - pad * 2) / layout.width;
    const sy = (el.clientHeight - pad * 2) / layout.height;
    if (sx >= sy) {
      fit();
      return;
    }
    minZoomRef.current = Math.min(0.12, sx);
    const z = clampZoom(Math.min(sy, 1.15), minZoomRef.current);
    const focus = layout.nodes.find((n) => n.focus) || layout.nodes[0];
    const cx = focus ? focus.x + (focus.w || CARD_W) / 2 : layout.width / 2;
    applyView({
      zoom: z,
      x: el.clientWidth / 2 - cx * z,
      y: (el.clientHeight - layout.height * z) / 2,
    });
  }

  useEffect(() => {
    let run = centerOnFocus;
    if (double || fan) {
      run = fit;
    } else if (entire) {
      run = lastEntireFocus && lastEntireFocus !== layout.focus ? centerOnFocus : fitTall;
      lastEntireFocus = layout.focus;
    } else {
      lastEntireFocus = null;
    }
    const id = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(run);
    });
    const t = window.setTimeout(run, 60);
    const t2 = window.setTimeout(run, 280);
    return () => {
      window.cancelAnimationFrame(id);
      window.clearTimeout(t);
      window.clearTimeout(t2);
    };
  }, [entire, double, fan, full, layout.focus, layout.width, layout.height]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => (double || fan ? fit() : entire ? fitTall() : centerOnFocus()));
    ro.observe(el);
    function onWheelNative(event) {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      applyZoom(zoomRef.current * wheelZoomFactor(event), {
        x: event.clientX - rect.left,
        y: event.clientY - rect.top,
      });
    }
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => {
      ro.disconnect();
      el.removeEventListener("wheel", onWheelNative);
    };
  }, [layout.focus, layout.width, layout.height, entire, double, fan]);

  function onPointerDown(event) {
    if (event.button != null && event.button !== 0) return;
    if (event.target.closest?.(".ged-zoom, .ged-full-bar")) return;
    event.preventDefault();
    movedRef.current = false;
    tapRef.current = event.target.closest?.("[data-person]")?.getAttribute("data-person") || null;
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: panRef.current.x,
      panY: panRef.current.y,
    };
    setDragging(true);
    const finish = (up) => {
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      if (dragRef.current && dragRef.current.pointerId === up.pointerId) onPointerUp(up);
    };
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  }

  function onPointerMove(event) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.buttons != null && event.buttons === 0) {
      onPointerUp(event);
      return;
    }
    const next = {
      x: drag.panX + (event.clientX - drag.x),
      y: drag.panY + (event.clientY - drag.y),
    };
    if (Math.abs(event.clientX - drag.x) > 4 || Math.abs(event.clientY - drag.y) > 4) {
      movedRef.current = true;
    }
    panRef.current = next;
    setPan(next);
  }

  function onPointerUp(event) {
    if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    tapRef.current = null;
  }

  // Big charts: only build the cards and lines near the viewport. The window
  // is quantised so small drags reuse the same set; a margin of one screen
  // keeps cards from popping in at the edges.
  const CULL_ABOVE = 200;
  let rectKey = "";
  if (layout.nodes.length > CULL_ABOVE) {
    const el = stageRef.current;
    const w = el?.clientWidth || 1400;
    const h = el?.clientHeight || 900;
    const q = 600;
    const x0 = Math.floor((-pan.x / zoom - w / zoom) / q) * q;
    const y0 = Math.floor((-pan.y / zoom - h / zoom) / q) * q;
    const x1 = Math.ceil((-pan.x / zoom + (2 * w) / zoom) / q) * q;
    const y1 = Math.ceil((-pan.y / zoom + (2 * h) / zoom) / q) * q;
    rectKey = `${x0}:${y0}:${x1}:${y1}`;
  }
  const visible = useMemo(() => {
    if (!rectKey) return layout;
    const [x0, y0, x1, y1] = rectKey.split(":").map(Number);
    const nodes = layout.nodes.filter(
      (n) => n.x + (n.w || CARD_W) >= x0 && n.x <= x1 && n.y + (n.h || CARD_H) >= y0 && n.y <= y1,
    );
    const edges = layout.edges.filter(
      (e) => Math.max(e.x1, e.x2) >= x0 && Math.min(e.x1, e.x2) <= x1 && Math.max(e.y1, e.y2) >= y0 && Math.min(e.y1, e.y2) <= y1,
    );
    return { ...layout, nodes, edges };
  }, [layout, rectKey]);

  // Cards and lines only rebuild when the layout or the visible window changes,
  // not on every pointer move.
  const canvas = useMemo(
    () => fan ? (
      <svg className="ged-fan" width={layout.width} height={layout.height}>
        {layout.nodes.map((node) => (
          <FanSegment key={node.id} node={node} onOpen={() => openFromClick(node.id)} />
        ))}
      </svg>
    ) : (
      <>
              <svg className="ged-lines" width={layout.width} height={layout.height} aria-hidden="true">
                <defs>
                  <marker
                    id="ged-arrow-down"
                    viewBox="0 0 10 10"
                    refX="5"
                    refY="9"
                    markerWidth="7"
                    markerHeight="7"
                    orient="0"
                  >
                    <path d="M 0 0 L 10 0 L 5 10 z" />
                  </marker>
                </defs>
                {visible.edges.map((edge, i) =>
                  edge.type === "marriage" ? (
                    <g key={`m-${i}`}>
                      <line x1={edge.x1} y1={edge.y1} x2={edge.x2} y2={edge.y2} />
                      {edge.label ? (
                        <text x={(edge.x1 + edge.x2) / 2} y={edge.y1 - 6} textAnchor="middle">
                          {edge.label}
                        </text>
                      ) : null}
                    </g>
                  ) : (
                    <line
                      key={`d-${i}`}
                      x1={edge.x1}
                      y1={edge.y1}
                      x2={edge.x2}
                      y2={edge.y2}
                      markerEnd={edge.type === "arrow" ? "url(#ged-arrow-down)" : undefined}
                    />
                  ),
                )}
              </svg>
              {visible.nodes.map((node) => (
                <button
                  key={node.id}
                  type="button"
                  className={`ged-card${node.focus ? " focus" : ""}${node.sex === "F" ? " female" : node.sex === "M" ? " male" : ""}${
                    node.compact ? " compact" : ""
                  }${node.branch ? ` branch-${node.branch}` : ""}${String(node.branch || "").startsWith("m") ? " left" : ""}`}
                  style={{ left: node.x, top: node.y, width: node.w || CARD_W, height: node.h || CARD_H }}
                  data-person={node.id}
                  onClick={() => openFromClick(node.id)}
                  title={cardName(node)}
                >
                  <span className="ged-card-photo">
                    {node.cover_url ? <img src={node.cover_url} alt="" loading="lazy" /> : <PersonGlyph />}
                  </span>
                  <span className="ged-card-head">{cardName(node)}</span>
                  <span className="ged-card-facts">
                    <CardFact label="Birth" event={node.birth} />
                    <CardFact label="Death" event={node.death} />
                    {node.occupation ? <CardFact label="Work" text={node.occupation} /> : null}
                    {!node.birth && !node.death && !node.occupation && node.lifespan ? (
                      <CardFact label="Lived" text={node.lifespan} />
                    ) : null}
                  </span>
                </button>
              ))}
      </>
    ),
    [visible, fan, layout],
  );

  return (
    <div className="ged-stage-wrap">
      <div
        className={`ged-stage${dragging ? " dragging" : ""}${full ? " full" : ""}`}
        ref={stageRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        {...tip("Scroll or pinch to zoom. Drag anywhere to move. Click a person to open their family chart.")}
      >
        <div
          className={`ged-tree${zoom < 0.55 ? " tiny" : ""}`}
          style={{
            width: layout.width,
            height: layout.height,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          }}
        >
          {canvas}
        </div>
      </div>
      {focusName ? (
        <div className="ged-chart-title" aria-hidden="true">
          <span className="ged-chart-kicker">{fan ? "Fan chart" : double ? "Double ancestor chart" : "Family chart"}</span>
          <strong>{focusName}</strong>
        </div>
      ) : null}
      {noAncestors ? (
        <p className="ged-empty-note" role="status">
          <strong>No ancestors to draw.</strong> The family tree file has no parents recorded for{" "}
          {focusName}, so this chart stops at them. Nothing is wrong with the chart or the file.
          Their spouse and children are on the Family view. To go further back, add their parents
          in your genealogy program and load the file again.
        </p>
      ) : null}
      <div className="ged-zoom zoom-tools" role="toolbar" aria-label="Chart tools">
        <div className="ged-panel-group" role="group" aria-label="View">
          <span className="ged-panel-label">View</span>
          {[
            ["one", "Family", "Parents, grandparents, brothers and sisters, spouse, and children."],
            ["all", "Entire tree", "Every person in this GEDCOM file."],
            ["double", "Double ancestors", "This person in the middle, the father's line to the right, the mother's to the left."],
            ["fan", "Fan chart", "Each generation a ring, father's line left and mother's right."],
          ].map(([id, label, hint]) => (
            <button
              key={id}
              type="button"
              className={mode === id ? undefined : "secondary"}
              aria-pressed={mode === id}
              onClick={() => onSetMode?.(id)}
              {...tip(hint)}
            >
              {label}
            </button>
          ))}
          {fan && onFanDepth ? (
            <div className="ged-zoom-row" role="group" aria-label="Generations">
              <button
                type="button"
                className="secondary"
                disabled={fanDepth <= 3}
                onClick={() => onFanDepth(-1)}
                {...tip("One generation fewer.")}
              >
                −
              </button>
              <span className="zoom-level" {...tip(`Rings shown: ${fanDepth}. Rings with people: ${fanRings}.`)}>
                {fanDepth} rings
              </span>
              <button
                type="button"
                className="secondary"
                disabled={fanDepth >= FAN_MAX_DEPTH}
                onClick={() => onFanDepth(1)}
                {...tip("One more generation.")}
              >
                +
              </button>
            </div>
          ) : null}
        </div>
        <div className="ged-panel-group" role="group" aria-label="Zoom">
          <span className="ged-panel-label">Zoom</span>
          <div className="ged-zoom-row">
            <button type="button" className="secondary" onClick={() => applyZoom(zoomRef.current / 1.2)} {...tip("Zoom out")}>
              −
            </button>
            <span className="zoom-level">{Math.round(zoom * 100)}%</span>
            <button type="button" className="secondary" onClick={() => applyZoom(zoomRef.current * 1.2)} {...tip("Zoom in")}>
              +
            </button>
          </div>
          <button
            type="button"
            className="secondary"
            onClick={entire ? fitTall : fit}
            {...tip(entire ? "Fit the tree's height and centre on this person. Drag sideways for the rest." : "Fit the whole chart in view.")}
          >
            Fit
          </button>
        </div>
        {onToggleFull ? (
          <button
            type="button"
            onClick={onToggleFull}
            {...tip(full ? "Leave fullscreen. Esc also works." : "See the chart on the whole screen.")}
          >
            {full ? "Exit full screen" : "Full screen"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

export default function Tree() {
  const [params, setParams] = useSearchParams();
  const nav = useNavigate();
  const selected = (params.get("p") || "").trim();
  const view = (params.get("view") || "").trim().toLowerCase();
  const entire = view === "all";
  const double = view === "double";
  const fanView = view === "fan";
  const fanDepth = Math.min(FAN_MAX_DEPTH, Math.max(3, Number(params.get("gens")) || FAN_DEPTH));
  const catalogFromUrl = (params.get("person") || "").trim();
  const fileRef = useRef(null);
  const [data, setData] = useState(null);
  const [catalogPeople, setCatalogPeople] = useState(null);
  const [person, setPerson] = useState(null);
  const [q, setQ] = useState("");
  const [pick, setPick] = useState(0);
  const [suggest, setSuggest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [full, setFull] = useState(false);
  const searchRef = useRef(null);
  const fullRef = useRef(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [personBusy, setPersonBusy] = useState(false);

  function openPerson(id) {
    const next = new URLSearchParams(params);
    if (id) next.set("p", id);
    else next.delete("p");
    // From the whole file, a click means "show me this person's own family".
    if (entire) next.delete("view");
    setParams(next, { replace: true });
  }

  async function loadList() {
    const next = await api.gedcom();
    setData(next);
    return next;
  }

  useEffect(() => {
    let cancel = false;
    loadList()
      .then((next) => {
        if (cancel) return;
        const wantCatalog = (new URLSearchParams(window.location.search).get("person") || "").trim();
        if (!selectedRef.current && !wantCatalog && next.loaded && next.people?.[0]?.id) {
          openPerson(next.people[0].id);
        }
      })
      .catch((ex) => {
        if (!cancel) setErr(ex.message);
      });
    api
      .people(undefined, { lite: true })
      .then((found) => {
        if (!cancel) setCatalogPeople((found.items || []).filter((p) => !p.unknown_name && p.name));
      })
      .catch(() => {
        if (!cancel) setCatalogPeople([]);
      });
    return () => {
      cancel = true;
    };
  }, []);

  useEffect(() => {
    const catalogId = catalogPersonId(selected);
    if (catalogId) {
      nav(`/people/${catalogId}`, { replace: true });
      return undefined;
    }
    if (!selected || !data?.loaded) {
      setPerson(null);
      setPersonBusy(false);
      return undefined;
    }
    let cancel = false;
    setPersonBusy(true);
    api
      .gedcomPerson(selected, entire ? { view: "all" } : {})
      .then((next) => {
        if (!cancel) setPerson(next);
      })
      .catch((ex) => {
        if (!cancel) {
          setErr(ex.message);
          setPerson(null);
        }
      })
      .finally(() => {
        if (!cancel) setPersonBusy(false);
      });
    return () => {
      cancel = true;
    };
  }, [selected, entire, data?.loaded, data?.filename]);

  useEffect(() => {
    if (!catalogFromUrl || !data?.loaded) return;
    const catalogId = Number(catalogFromUrl);
    if (!catalogId) return;
    const named = (catalogPeople || []).find((p) => Number(p.id) === catalogId);
    const treeId = treePersonIdForCatalog(data.people, catalogId, named?.name);
    if (!treeId && catalogPeople == null) return;
    const next = new URLSearchParams(params);
    next.delete("person");
    if (treeId) next.set("p", treeId);
    setParams(next, { replace: true });
    if (!treeId) {
      setErr("That person is not in the family tree.");
      if (named?.name) setQ(named.name);
    }
  }, [catalogFromUrl, catalogPeople, data, params, setParams]);

  const catalogIdsInTree = useMemo(() => {
    const ids = new Set();
    for (const item of data?.people || []) {
      if (item.catalog_id) ids.add(item.catalog_id);
    }
    return ids;
  }, [data]);

  const matches = useMemo(() => {
    const items = data?.people || [];
    const needle = q.trim().toLowerCase();
    if (!needle) return items;
    const treeHits = items
      .map((item) => ({ item, score: scorePerson(item, needle) }))
      .filter((row) => row.score > 0);
    const linked = new Set(treeHits.map((row) => row.item.catalog_id).filter(Boolean));
    const catalogHits = (catalogPeople || [])
      .filter((person) => !catalogIdsInTree.has(person.id) && !linked.has(person.id))
      .map((person) => ({
        item: {
          id: catalogHitId(person.id),
          name: person.name,
          nickname: person.nickname || "",
          surname: "",
          lifespan: "",
          source: "catalog",
          catalog_id: person.id,
        },
        score: scorePerson(person, needle),
      }))
      .filter((row) => row.score > 0);
    return [...treeHits, ...catalogHits]
      .sort((a, b) => b.score - a.score || a.item.name.localeCompare(b.item.name))
      .map((row) => row.item);
  }, [data, q, catalogPeople, catalogIdsInTree]);
  const filtered = q.trim() ? matches : data?.people || [];
  const suggestions = q.trim() ? matches.slice(0, 12) : [];

  function treeIdForJump(id) {
    const catalogId = catalogPersonId(id);
    if (!catalogId) return id;
    const people = data?.people || [];
    const linked = people.find((p) => Number(p.catalog_id) === Number(catalogId));
    if (linked?.id) return linked.id;
    const hit = matches.find((p) => String(p.id) === String(id));
    const needle = String(hit?.name || "").trim().toLowerCase();
    if (!needle) return "";
    const named = people.filter((p) => String(p.name || "").trim().toLowerCase() === needle);
    return named.length === 1 ? named[0].id : "";
  }

  function jumpTo(id) {
    if (!id) return;
    setSuggest(false);
    setPick(0);
    const treeId = treeIdForJump(id);
    if (treeId) {
      // A search jump shows that person's own family, not the whole file at 1%.
      const next = new URLSearchParams(params);
      next.set("p", treeId);
      next.delete("view");
      setParams(next, { replace: true });
      return;
    }
    const catalogId = catalogPersonId(id);
    if (catalogId) {
      nav(`/people/${catalogId}`);
    }
  }

  async function onFile(file) {
    if (!file) return;
    setErr("");
    setBusy(true);
    try {
      const next = await api.uploadGedcom(file);
      setData(next);
      const first = next.people?.[0]?.id;
      if (first) openPerson(first);
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function onClear() {
    setErr("");
    setBusy(true);
    try {
      await api.clearGedcom();
      setData({ loaded: false, people: [] });
      setPerson(null);
      openPerson("");
    } catch (ex) {
      setErr(ex.message);
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    setPick(0);
  }, [q]);

  useEffect(() => {
    const list = document.querySelector(".ged-list");
    const node = list?.querySelector(".ged-person.active");
    if (!list || !node) return;
    const nodeRect = node.getBoundingClientRect();
    const listRect = list.getBoundingClientRect();
    if (nodeRect.top >= listRect.top && nodeRect.bottom <= listRect.bottom) return;
    list.scrollTop += nodeRect.top - listRect.top - list.clientHeight / 2 + nodeRect.height / 2;
  }, [selected, filtered]);

  function enterFull() {
    setFull(true);
    window.setTimeout(() => enterBrowserFullscreen(fullRef.current || document.documentElement), 0);
  }

  function exitFull() {
    setFull(false);
    exitBrowserFullscreen();
  }

  useEffect(() => {
    if (!full) return undefined;
    document.body.style.overflow = "hidden";
    function onKey(event) {
      if (event.key !== "Escape") return;
      if (suggest) return;
      event.preventDefault();
      exitFull();
    }
    function onFs() {
      if (!document.fullscreenElement && !document.webkitFullscreenElement) {
        setFull(false);
      }
    }
    window.addEventListener("keydown", onKey);
    document.addEventListener("fullscreenchange", onFs);
    document.addEventListener("webkitfullscreenchange", onFs);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("fullscreenchange", onFs);
      document.removeEventListener("webkitfullscreenchange", onFs);
    };
  }, [full, suggest]);

  const loaded = Boolean(data?.loaded);

  return (
    <div className="ged-page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Genealogy</p>
          <h1>Family tree</h1>
          <p className="lede">
            Open a GEDCOM file (.ged) from Ancestry, FamilySearch, Gramps, or MacFamilyTree. The
            file is stored on this Mac. Photo originals are not touched.
          </p>
        </div>
        <div className="page-head-actions">
          {loaded && !full ? (
            <NameSearch
              q={q}
              setQ={setQ}
              suggestions={suggestions}
              pick={pick}
              setPick={setPick}
              suggest={suggest}
              setSuggest={setSuggest}
              onJump={jumpTo}
              searchRef={searchRef}
            />
          ) : null}
          <input
            ref={fileRef}
            type="file"
            accept=".ged,.gedcom,text/plain"
            hidden
            onChange={(e) => onFile(e.target.files?.[0])}
          />
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            {...tip("Load a .ged family tree exported from your genealogy app.")}
          >
            {busy ? "Loading…" : loaded ? "Replace file" : "Choose .ged file"}
          </button>
          {loaded ? (
            <button type="button" className="secondary" onClick={onClear} disabled={busy}>
              Remove
            </button>
          ) : null}
        </div>
      </div>

      {err ? <p className="error">{err}</p> : null}

      {!loaded ? (
        <div className="card empty">
          No family tree is loaded yet. Choose a .ged file to see how people connect.
        </div>
      ) : (
        <>
          <p className="hint ged-meta">
            {data.filename}
            {data.source ? ` · ${data.source}` : ""}
            {` · ${data.people_count} ${data.people_count === 1 ? "person" : "people"}`}
            {` · ${data.families_count} ${data.families_count === 1 ? "family" : "families"}`}
          </p>
          <div className="ged-layout">
            <div className="ged-list card">
              <p className="hint ged-list-count">
                {q.trim()
                  ? `${matches.length} match${matches.length === 1 ? "" : "es"}`
                  : `${data.people_count} people`}
              </p>
              <ul>
                {filtered.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={`ged-person${item.id === selected ? " active" : ""}`}
                      onClick={() => jumpTo(item.id)}
                    >
                      <span className="ged-person-name">{cardName(item)}</span>
                      {item.lifespan ? <span className="hint">{item.lifespan}</span> : null}
                      {item.source === "catalog" ? (
                        <span className="ged-in-catalog">In photos</span>
                      ) : item.catalog_id ? (
                        <span className="ged-in-catalog">In catalog</span>
                      ) : null}
                    </button>
                  </li>
                ))}
              </ul>
              {!filtered.length ? <p className="hint">No names match that search.</p> : null}
            </div>
            <div className={`ged-board${full ? " full" : ""}`} ref={fullRef}>
              {full ? (
                <div className="ged-full-bar">
                  <button type="button" className="secondary" onClick={exitFull} {...tip("Leave fullscreen. Esc also works.")}>
                    Exit
                  </button>
                  <NameSearch
                    q={q}
                    setQ={setQ}
                    suggestions={suggestions}
                    pick={pick}
                    setPick={setPick}
                    suggest={suggest}
                    setSuggest={setSuggest}
                    onJump={jumpTo}
                  />
                  {person ? (
                    <p className="ged-full-name">
                      <strong>{cardName(person)}</strong>
                      {person.lifespan ? ` · ${person.lifespan}` : ""}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {person ? (
                <>
                  <FamilyChart
                    key={`${person.id}:${fanView ? "fan" : double ? "double" : entire ? "all" : "one"}`}
                    chart={person.chart}
                    onOpen={openPerson}
                    full={full}
                    entire={entire}
                    mode={fanView ? "fan" : double ? "double" : entire ? "all" : "one"}
                    onSetMode={(next_mode) => {
                      const next = new URLSearchParams(params);
                      if (next_mode === "one") next.delete("view");
                      else next.set("view", next_mode);
                      setParams(next, { replace: true });
                    }}
                    fanDepth={fanDepth}
                    onFanDepth={(delta) => {
                      const next = new URLSearchParams(params);
                      next.set("gens", String(Math.min(FAN_MAX_DEPTH, Math.max(3, fanDepth + delta))));
                      setParams(next, { replace: true });
                    }}
                    onToggleFull={full ? exitFull : enterFull}
                  />
                  {full ? null : (
                    <div className="ged-caption">
                      <strong>{cardName(person)}</strong>
                      {person.lifespan ? ` · ${person.lifespan}` : ""}
                      {person.birth?.place ? ` · born ${person.birth.place}` : ""}
                      {person.catalog ? (
                        <>
                          {" · "}
                          <Link to={`/people/${person.catalog.id}`}>Open in Faces in DB View</Link>
                        </>
                      ) : null}
                      {person.note ? <p className="ged-note">{person.note}</p> : null}
                    </div>
                  )}
                </>
              ) : (
                <p className="hint">
                  {personBusy || selected ? "Drawing this tree…" : "Choose a person on the left."}
                </p>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
