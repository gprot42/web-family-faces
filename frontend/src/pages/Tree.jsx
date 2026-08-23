import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { CARD_H, CARD_W, clampZoom, layoutFamilyTree, viewOnFocus, wheelZoomFactor } from "../familyChart.js";
import { queryMatchesName } from "../nameSuggest.js";
import { enterBrowserFullscreen, exitBrowserFullscreen } from "../play.js";
import { tip } from "../tip.js";

function shortName(name) {
  const text = String(name || "");
  return text.length > 22 ? `${text.slice(0, 21)}…` : text;
}

function scorePerson(person, needle) {
  const name = String(person?.name || "").toLowerCase();
  const surname = String(person?.surname || "").toLowerCase();
  const nick = String(person?.nickname || "").toLowerCase();
  const hay = `${name} ${surname} ${nick}`.replace(/\s+/g, " ").trim();
  if (!queryMatchesName(needle, hay)) return 0;
  const tokens = needle.split(/\s+/).filter(Boolean);
  const words = `${name} ${nick}`.split(/\s+/).filter(Boolean);
  if (name === needle || nick === needle) return 100;
  if (name.startsWith(needle) || nick.startsWith(needle)) return 90;
  if (surname.startsWith(needle)) return 85;
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
                <span>{item.name}</span>
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

function FamilyChart({ chart, onOpen, full, onToggleFull }) {
  const layout = useMemo(() => layoutFamilyTree(chart), [chart]);
  const stageRef = useRef(null);
  const zoomRef = useRef(1);
  const panRef = useRef({ x: 0, y: 0 });
  const dragRef = useRef(null);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);

  function applyZoom(next, origin) {
    const z = clampZoom(next);
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
    const z = clampZoom(Math.min(sx, sy, 1.15));
    applyView({
      zoom: z,
      x: (el.clientWidth - layout.width * z) / 2,
      y: (el.clientHeight - layout.height * z) / 2,
    });
  }

  useEffect(() => {
    const id = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(centerOnFocus);
    });
    const t = window.setTimeout(centerOnFocus, 60);
    const t2 = window.setTimeout(centerOnFocus, 280);
    return () => {
      window.cancelAnimationFrame(id);
      window.clearTimeout(t);
      window.clearTimeout(t2);
    };
  }, [full, layout.focus, layout.width, layout.height]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => centerOnFocus());
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
  }, [layout.focus, layout.width, layout.height]);

  function onPointerDown(event) {
    if (event.button != null && event.button !== 0) return;
    if (event.target.closest?.(".ged-card, .ged-zoom, .ged-full-bar")) return;
    event.preventDefault();
    dragRef.current = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      panX: panRef.current.x,
      panY: panRef.current.y,
    };
    setDragging(true);
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function onPointerMove(event) {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    const next = {
      x: drag.panX + (event.clientX - drag.x),
      y: drag.panY + (event.clientY - drag.y),
    };
    panRef.current = next;
    setPan(next);
  }

  function onPointerUp(event) {
    if (!dragRef.current || dragRef.current.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
  }

  return (
    <div className="ged-stage-wrap">
      <div
        className={`ged-stage${dragging ? " dragging" : ""}${full ? " full" : ""}`}
        ref={stageRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        {...tip("Scroll or pinch to zoom. Drag the background to move. Click a person to center the tree on them.")}
      >
        <div
          className="ged-tree"
          style={{
            width: layout.width,
            height: layout.height,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          }}
        >
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
            {layout.edges.map((edge, i) =>
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
          {layout.nodes.map((node) => (
            <button
              key={node.id}
              type="button"
              className={`ged-card${node.focus ? " focus" : ""}${node.sex === "F" ? " female" : node.sex === "M" ? " male" : ""}`}
              style={{ left: node.x, top: node.y, width: CARD_W, height: CARD_H }}
              onClick={() => onOpen(node.id)}
              title={node.name}
            >
              <span className="ged-card-name">{shortName(node.name)}</span>
              <span className="ged-card-life">{node.lifespan || " "}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="ged-zoom zoom-tools">
        <button type="button" onClick={() => applyZoom(zoomRef.current * 1.2)} {...tip("Zoom in")}>
          +
        </button>
        <button type="button" className="zoom-level" onClick={fit} {...tip("Fit the whole tree in view")}>
          {Math.round(zoom * 100)}%
        </button>
        <button type="button" onClick={() => applyZoom(zoomRef.current / 1.2)} {...tip("Zoom out")}>
          −
        </button>
        <button type="button" onClick={fit} {...tip("Fit the whole tree in view")}>
          Fit
        </button>
        {onToggleFull ? (
          <button
            type="button"
            onClick={onToggleFull}
            {...tip(full ? "Leave fullscreen. Esc also works." : "See the tree on the whole screen.")}
          >
            {full ? "Exit" : "Full screen"}
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
  const fileRef = useRef(null);
  const [data, setData] = useState(null);
  const [catalogPeople, setCatalogPeople] = useState([]);
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
        if (!selectedRef.current && next.loaded && next.people?.[0]?.id) {
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
      .catch(() => {});
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
      .gedcomPerson(selected)
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
  }, [selected, data?.loaded, data?.filename]);

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
    const catalogHits = catalogPeople
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
      openPerson(treeId);
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
                      <span className="ged-person-name">{item.name}</span>
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
                      <strong>{person.name}</strong>
                      {person.lifespan ? ` · ${person.lifespan}` : ""}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {person ? (
                <>
                  <FamilyChart
                    key={person.id}
                    chart={person.chart}
                    onOpen={openPerson}
                    full={full}
                    onToggleFull={full ? exitFull : enterFull}
                  />
                  {full ? null : (
                    <div className="ged-caption">
                      <strong>{person.name}</strong>
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
