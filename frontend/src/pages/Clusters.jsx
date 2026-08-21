import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";
import { groupWhen } from "../ages.js";
import { matchPeople, uniqueFirstName } from "../nameSuggest.js";
import FamousLookup from "../components/FamousLookup.jsx";
import JobGauge from "../components/JobGauge.jsx";
import NameSuggest from "../components/NameSuggest.jsx";
import PersonPicker, { isPersonDrag, personFromDataTransfer } from "../components/PersonPicker.jsx";
import { saveCachedPeople } from "../peopleCache.js";

function clusterKey(cluster) {
  const ids = cluster.face_ids;
  if (ids?.length) return `f-${Math.min(...ids)}`;
  return `c-${cluster.id}`;
}

function useNearViewport(startNear, rootRef) {
  const ref = useRef(null);
  const [near, setNear] = useState(Boolean(startNear));
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const io = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), {
      root: rootRef?.current || null,
      rootMargin: "400px 0px",
    });
    io.observe(node);
    return () => io.disconnect();
  }, [rootRef]);
  return [ref, near];
}

const CATEGORIES = [
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

const POS_KEY = "photosort-to-name-pos";
const NAMED_W_KEY = "photosort-to-name-named-w";
const HAS_NAMED_KEY = "photosort-to-name-has-named";
const NAMED_W_MIN = 280;
const NAMED_W_DEFAULT = 340;
const NAMED_W_MAX = 720;
const GROUPS_MIN = 480;

function readToNamePos() {
  try {
    return JSON.parse(sessionStorage.getItem(POS_KEY) || "null");
  } catch {
    return null;
  }
}

function writeToNamePos(pos) {
  try {
    const prev = readToNamePos() || {};
    sessionStorage.setItem(POS_KEY, JSON.stringify({ ...prev, ...pos }));
  } catch {
    /* ignore quota */
  }
}

function applyToNamePos(el, pos) {
  if (!el || !pos) return false;
  const crop = pos.faceId ? el.querySelector(`[data-face-id="${pos.faceId}"]`) : null;
  const card = pos.clusterId ? el.querySelector(`[data-cluster-id="${pos.clusterId}"]`) : null;
  const target = crop || card;
  if (target) {
    target.scrollIntoView({ block: "center", inline: "nearest" });
    return true;
  }
  if (pos.scrollTop != null && Number(pos.scrollTop) > 0) {
    el.scrollTop = Number(pos.scrollTop) || 0;
    return true;
  }
  return false;
}

function readHasNamed() {
  try {
    return localStorage.getItem(HAS_NAMED_KEY) === "1";
  } catch {
    return false;
  }
}

function writeHasNamed(on) {
  try {
    localStorage.setItem(HAS_NAMED_KEY, on ? "1" : "0");
  } catch {
    /* private mode */
  }
}

function readNamedWidth() {
  try {
    const n = Number(localStorage.getItem(NAMED_W_KEY));
    if (Number.isFinite(n) && n >= NAMED_W_MIN) return Math.round(n);
  } catch {
    /* private mode */
  }
  return NAMED_W_DEFAULT;
}

function writeNamedWidth(w) {
  try {
    localStorage.setItem(NAMED_W_KEY, String(Math.round(w)));
  } catch {
    /* private mode */
  }
}

function namedMax(layoutEl) {
  const layoutW = layoutEl?.clientWidth || 0;
  if (!layoutW) return NAMED_W_MAX;
  return Math.max(NAMED_W_MIN, Math.min(NAMED_W_MAX, layoutW - GROUPS_MIN));
}

function clampNamedWidth(w, layoutEl) {
  const n = Number(w);
  const fallback = Number.isFinite(n) ? n : NAMED_W_DEFAULT;
  return Math.round(Math.min(namedMax(layoutEl), Math.max(NAMED_W_MIN, fallback)));
}

export default function Clusters({ onChange, stats }) {
  const [items, setItems] = useState([]);
  const [people, setPeople] = useState([]);
  const [err, setErr] = useState("");
  const [saveErr, setSaveErr] = useState(null);
  const [savingId, setSavingId] = useState(null);
  const [saved, setSaved] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showCats, setShowCats] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [catById, setCatById] = useState({});
  const groupsRef = useRef(null);
  const layoutRef = useRef(null);
  const namedNow = useRef(readNamedWidth());
  const dragNamed = useRef(null);
  const [namedW, setNamedW] = useState(() => namedNow.current);
  const [resizingNamed, setResizingNamed] = useState(false);
  const [peopleReady, setPeopleReady] = useState(false);
  const [hasNamedHint] = useState(() => readHasNamed());
  const restoredPos = useRef(false);
  const [job, setJob] = useState(null);
  const [lookupOk, setLookupOk] = useState(true);
  const [identifyNote, setIdentifyNote] = useState("");
  const jobKey = useRef("");

  useEffect(() => {
    api
      .health()
      .then((h) => setLookupOk(Boolean(h.lookup?.available)))
      .catch(() => setLookupOk(false));
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        const data = await api.jobs();
        if (cancelled) return;
        const active = data?.active;
        const latest = data?.recent?.[0];
        const current =
          active?.type === "identify"
            ? active
            : !active && latest?.type === "identify" && latest.status === "paused"
              ? latest
              : null;
        setJob(current);
        if (current && (current.status === "running" || current.status === "queued")) {
          const key = `${current.id}-${current.progress}-${current.message || ""}`;
          if (jobKey.current !== key) {
            jobKey.current = key;
            refresh().catch(() => {});
          }
        } else if (!current && jobKey.current) {
          jobKey.current = "";
          const latest = data?.recent?.[0];
          if (latest?.type === "identify" && latest.status === "done" && latest.message) {
            setIdentifyNote(latest.message);
          }
          refresh().catch(() => {});
          onChange?.();
        }
      } catch {
        /* backend may be starting */
      }
    }
    tick();
    const id = setInterval(tick, 8000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  async function refresh() {
    const peopleP = api
      .people(undefined, { lite: true })
      .then((p) => {
        const items = p.items || [];
        setPeople(items);
        setPeopleReady(true);
        writeHasNamed(items.some((person) => !person.unknown_name));
        saveCachedPeople("", items);
      })
      .catch(() => {
        setPeopleReady(true);
      });
    const c = await api.clusters();
    setItems(c.items || []);
    peopleP.catch(() => {});
    return Boolean(c.clustering) && !(c.items || []).length;
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        while (!cancelled && (await refresh())) {
          await new Promise((r) => setTimeout(r, 1500));
        }
      } catch (ex) {
        if (!cancelled) setErr(ex.message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!items.length) {
      setActiveId(null);
      return;
    }
    if (!items.some((item) => item.id === activeId)) setActiveId(items[0].id);
  }, [items, activeId]);

  function rememberPos(extra = {}) {
    const el = groupsRef.current;
    writeToNamePos({
      scrollTop: el ? el.scrollTop : window.scrollY,
      activeId,
      ...extra,
    });
  }

  useEffect(() => {
    const el = groupsRef.current;
    if (!el) return undefined;
    function onScroll() {
      writeToNamePos({
        scrollTop: el.scrollTop,
        activeId,
      });
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      onScroll();
      el.removeEventListener("scroll", onScroll);
    };
  }, [activeId]);

  function applyNamedWidth(next) {
    const w = clampNamedWidth(next, layoutRef.current);
    namedNow.current = w;
    setNamedW(w);
    return w;
  }

  useEffect(() => {
    applyNamedWidth(namedNow.current);
    function onWin() {
      applyNamedWidth(namedNow.current);
    }
    window.addEventListener("resize", onWin);
    return () => {
      window.removeEventListener("resize", onWin);
      document.documentElement.classList.remove("to-name-resizing");
    };
  }, []);

  function onNamedResizeStart(e) {
    if (e.button != null && e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    dragNamed.current = {
      x: e.clientX,
      w: namedNow.current,
      max: namedMax(layoutRef.current),
      pointerId: e.pointerId,
    };
    setResizingNamed(true);
    document.documentElement.classList.add("to-name-resizing");
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }

  function onNamedResizeMove(e) {
    const drag = dragNamed.current;
    if (!drag || drag.pointerId !== e.pointerId) return;
    applyNamedWidth(drag.w + (drag.x - e.clientX));
  }

  function onNamedResizeEnd(e) {
    const drag = dragNamed.current;
    if (!drag || (e.pointerId != null && drag.pointerId !== e.pointerId)) return;
    dragNamed.current = null;
    setResizingNamed(false);
    document.documentElement.classList.remove("to-name-resizing");
    writeNamedWidth(namedNow.current);
    try {
      e.currentTarget.releasePointerCapture?.(e.pointerId);
    } catch {
      /* already released */
    }
    if (e.pointerType !== "keyboard") e.currentTarget.blur?.();
  }

  function onNamedResizeKey(e) {
    const step = e.shiftKey ? 64 : 24;
    if (e.key === "ArrowLeft") {
      e.preventDefault();
      writeNamedWidth(applyNamedWidth(namedNow.current + step));
    } else if (e.key === "ArrowRight") {
      e.preventDefault();
      writeNamedWidth(applyNamedWidth(namedNow.current - step));
    } else if (e.key === "Home") {
      e.preventDefault();
      writeNamedWidth(applyNamedWidth(NAMED_W_DEFAULT));
    } else if (e.key === "End") {
      e.preventDefault();
      writeNamedWidth(applyNamedWidth(namedMax(layoutRef.current)));
    }
  }

  function onNamedResizeReset() {
    writeNamedWidth(applyNamedWidth(NAMED_W_DEFAULT));
  }

  useEffect(() => {
    if (loading || !items.length || restoredPos.current) return;
    const pos = readToNamePos();
    if (!pos) {
      restoredPos.current = true;
      return undefined;
    }
    const el = groupsRef.current;
    if (!el) return undefined;
    if (pos.activeId && items.some((item) => item.id === pos.activeId)) {
      setActiveId(pos.activeId);
    }
    let tries = 0;
    let timer = 0;
    const tryApply = () => {
      if (applyToNamePos(el, pos) || tries >= 16) {
        restoredPos.current = true;
        return;
      }
      tries += 1;
      timer = window.setTimeout(tryApply, 50);
    };
    const raf = requestAnimationFrame(() => requestAnimationFrame(tryApply));
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
    };
  }, [loading, items]);

  function faceIdsOf(id) {
    const cluster = items.find((item) => item.id === id);
    if (!cluster) return [];
    if (cluster.face_ids?.length) return cluster.face_ids;
    if (cluster.faces?.length) return cluster.faces.map((f) => f.id);
    return [];
  }

  async function failSave(id, ex, extra = {}) {
    const message = ex.message || "Could not save.";
    setSaved(null);
    setErr(message);
    setSaveErr({ id, message });
    api.reportError(message, { page: "to-name", cluster_id: id, ...extra }).catch(() => {});
    await refresh();
    requestAnimationFrame(() => {
      const root = groupsRef.current;
      const card = root?.querySelector(`[data-cluster-id="${id}"]`);
      const banner = card?.querySelector(".save-error") || root?.querySelector(".save-error") || card;
      banner?.scrollIntoView({ block: "center", inline: "nearest" });
    });
  }

  async function nameCluster(id, name, category) {
    setErr("");
    setSaveErr(null);
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setSavingId(id);
    try {
      const result = await api.nameCluster(id, name, faceIds, category);
      if (!result.assigned) {
        throw new Error(
          result.message ||
            "Could not attach that name to these faces. If they are someone already in the catalog, click that person. Otherwise mark mixed faces Not this person and try again.",
        );
      }
      setItems((cur) => cur.filter((c) => c.id !== id));
      setSaved({
        name: result.person?.name || name,
        faces: result.assigned || namedCount,
        also: result.also_matched || 0,
        leftover: result.remaining || 0,
      });
      onChange?.({
        unknown_clusters: result.remaining ? 0 : -1,
        faces_unknown: -(result.assigned || namedCount),
        people: 1,
        people_named: 1,
      });
      await refresh();
      onChange?.();
    } catch (ex) {
      await failSave(id, ex, { action: "name" });
    } finally {
      setSavingId(null);
    }
  }

  async function markJunk(id) {
    setErr("");
    setSaveErr(null);
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setItems((cur) => cur.filter((c) => c.id !== id));
    setSaved({ name: "Not a person", faces: namedCount, also: 0 });
    onChange?.({ unknown_clusters: -1, faces_unknown: -namedCount });
    try {
      const result = await api.junkCluster(id, faceIds);
      if (!result.cleared) {
        throw new Error(result.message || "That group was regrouped. Click Not a person again.");
      }
      await refresh();
      onChange?.();
    } catch (ex) {
      await failSave(id, ex, { action: "junk" });
    }
  }

  async function markUnknown(id, category) {
    setErr("");
    setSaveErr(null);
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setItems((cur) => cur.filter((c) => c.id !== id));
    setSaved({ name: "Unknown name of person", faces: namedCount, also: 0 });
    onChange?.({
      unknown_clusters: -1,
      faces_unknown: -namedCount,
      people: 1,
      people_unknown: 1,
    });
    try {
      const result = await api.unknownCluster(id, category, faceIds);
      if (!result.assigned) {
        throw new Error(result.message || "That group was regrouped. Click Unknown again.");
      }
      await refresh();
      onChange?.();
    } catch (ex) {
      await failSave(id, ex, { action: "unknown" });
    }
  }

  async function assignToPerson(id, person, category) {
    setErr("");
    setSaveErr(null);
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setSavingId(id);
    try {
      const result = await api.assignCluster(id, person.id, faceIds, category);
      if (!result.assigned) {
        throw new Error(
          result.message ||
            "Could not attach that catalog name to these faces. Mark mixed faces Not this person and try again.",
        );
      }
      setItems((cur) => cur.filter((c) => c.id !== id));
      setSaved({
        name: person.name,
        faces: result.assigned || namedCount,
        also: result.also_matched || 0,
        leftover: result.remaining || 0,
      });
      onChange?.({ unknown_clusters: result.remaining ? 0 : -1, faces_unknown: -(result.assigned || namedCount) });
      await refresh();
      onChange?.();
    } catch (ex) {
      await failSave(id, ex, { action: "assign" });
    } finally {
      setSavingId(null);
    }
  }

  const identifying = Boolean(job && (job.status === "running" || job.status === "queued"));
  const identifyPaused = Boolean(job && job.status === "paused");
  const hasNamedPeople = people.some((p) => !p.unknown_name);
  const hasNamed =
    hasNamedPeople ||
    ((loading || items.length > 0) && (hasNamedHint || Number(stats?.people_named) > 0));

  async function startIdentify() {
    setErr("");
    setIdentifyNote("");
    try {
      const started = await api.identify();
      if (started?.type && started.type !== "identify") {
        setErr("Wait for Find Known Faces to finish, then try Identify all.");
        return;
      }
      setJob(started);
    } catch (ex) {
      setErr(ex.message || "Could not start Identify all.");
    }
  }

  return (
    <div className="to-name-page">
      <div
        ref={layoutRef}
        className={`to-name-layout${hasNamed ? " has-named" : ""}${resizingNamed ? " resizing" : ""}`}
        style={hasNamed ? { "--named-panel-w": `${namedW}px` } : undefined}
      >
        <div className="to-name-groups" ref={groupsRef}>
          <div className={`to-name-sticky${identifying || identifyPaused ? " busy" : ""}`}>
            <div className="page-head">
              <div>
                <p className="eyebrow">Inbox</p>
                <h1>Faces to name</h1>
                {identifying || identifyPaused ? null : (
                  <p className="lede">
                    Each group is faces that look like one person at a similar age. Name the group on the
                    right. Identify all first matches people already in the catalog, then looks remaining
                    groups up. AI on a group looks that one up. Catalog matches still go to Check names.
                    You can keep naming by hand while it runs. If two people were mixed, click the extra
                    faces on the left.
                  </p>
                )}
              </div>
              {identifying || identifyPaused ? null : (
                <div className="page-head-actions">
                  <button
                    type="button"
                    disabled={loading}
                    onClick={startIdentify}
                    {...tip(
                      lookupOk
                        ? "Name every unnamed group that looks sure: people already in the catalog first, then AI lookup. Check names still lists auto matches. Mixed or large groups are skipped. Use AI on a group to look that one up."
                        : "Names groups that match people already in the catalog. Add an xAI key or SuperGrok in Settings to also look remaining groups up.",
                    )}
                  >
                    Identify all
                  </button>
                </div>
              )}
            </div>
            {identifying || identifyPaused ? (
              <JobGauge job={job} title="Identifying faces" resumeable={false} compact />
            ) : null}
          </div>
          {err ? (
            <p className="error save-error" role="alert">
              {err}
            </p>
          ) : null}
          {identifyNote ? (
            <div className="save-note" role="status">
              {identifyNote}. Groups named from the catalog also appear under Check names.
            </div>
          ) : null}
          {saved ? (
            <div className="save-note" role="status" aria-live="polite">
              Saved {saved.name}. {saved.faces} face{saved.faces === 1 ? "" : "s"} in that group
              {saved.also ? ` · ${saved.also} more matched` : ""}.
              {saved.leftover
                ? ` ${saved.leftover} face${saved.leftover === 1 ? "" : "s"} still unnamed in that group — name or split them below.`
                : items.length
                  ? " Next group is below."
                  : " Nothing left to name here."}
            </div>
          ) : null}
          {loading ? (
            <div className="card empty">Grouping faces that look like the same person…</div>
          ) : null}
          {!loading && items.length === 0 && !saved ? (
            <div className="card empty">
              Nothing grouped to name yet. Find Known Faces is still searching photos — open this page
              again in a minute, or name people on Faces in DB View.
            </div>
          ) : null}
          {items.map((c, i) => (
            <ClusterCard
              key={clusterKey(c)}
              cluster={c}
              people={people}
              saving={savingId === c.id}
              saveError={saveErr?.id === c.id ? saveErr.message : ""}
              eager={i === 0}
              active={c.id === activeId}
              category={catById[c.id] || ""}
              onCategory={(next) => setCatById((cur) => ({ ...cur, [c.id]: next }))}
              onActivate={() => setActiveId(c.id)}
              onName={nameCluster}
              onAssign={assignToPerson}
              onUnknown={markUnknown}
              onJunk={markJunk}
              scrollRoot={groupsRef}
              onOpenPhoto={(face) =>
                rememberPos({ clusterId: c.id, activeId: c.id, faceId: face?.id })
              }
              onChange={refresh}
              lookupOk={lookupOk}
            />
          ))}
        </div>
        {hasNamed ? (
          <aside className="to-name-named">
            <div
              className="to-name-named-resize"
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize named people list"
              aria-valuemin={NAMED_W_MIN}
              aria-valuemax={namedMax(layoutRef.current)}
              aria-valuenow={namedW}
              tabIndex={0}
              onPointerDown={onNamedResizeStart}
              onPointerMove={onNamedResizeMove}
              onPointerUp={onNamedResizeEnd}
              onPointerCancel={onNamedResizeEnd}
              onKeyDown={onNamedResizeKey}
              onDoubleClick={onNamedResizeReset}
              {...tip("Drag left to show more people. Double-click to reset.")}
            />
            <div className="cluster-or">or click or drag someone already named</div>
            <p className="hint">
              Applies to the highlighted group
              {items.find((c) => c.id === activeId)
                ? ` · ${items.find((c) => c.id === activeId).face_count} face${items.find((c) => c.id === activeId).face_count === 1 ? "" : "s"}`
                : ""}
              .
            </p>
            {peopleReady ? (
              <PersonPicker
                people={people}
                showCategoryFilter
                categoryFilter={showCats}
                onCategoryFilter={setShowCats}
                hint="Click to name the highlighted group, or drag onto Name this person."
                onPick={(p) => {
                  const target = items.find((c) => c.id === activeId) || items[0];
                  if (target) assignToPerson(target.id, p, catById[target.id] || "");
                }}
              />
            ) : (
              <p className="hint">Loading names…</p>
            )}
          </aside>
        ) : null}
      </div>
    </div>
  );
}

function ClusterCard({
  cluster,
  people,
  saving,
  saveError,
  eager,
  active,
  category,
  onCategory,
  onActivate,
  onName,
  onAssign,
  onUnknown,
  onJunk,
  onOpenPhoto,
  onChange,
  scrollRoot,
  lookupOk = true,
}) {
  const [name, setName] = useState("");
  const [namePick, setNamePick] = useState(-1);
  const [picked, setPicked] = useState([]);
  const [dropOver, setDropOver] = useState(false);
  const dragDepth = useRef(0);
  const [cardRef, near] = useNearViewport(eager, scrollRoot);
  const canSplit = cluster.faces.length > 1;
  const canSave = Boolean(name.trim()) && !saving;
  const catalogHits = matchPeople(name, people);
  const highlightedCatalog = catalogPerson(true);

  function toggle(id) {
    setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

  function exactCatalogPerson() {
    const q = name.trim().toLowerCase();
    if (!q) return null;
    return (
      people.find((p) => !p.unknown_name && String(p.name || "").trim().toLowerCase() === q) || null
    );
  }

  function catalogPerson(useHighlight = false) {
    if (useHighlight && namePick >= 0 && catalogHits[namePick]) return catalogHits[namePick];
    return uniqueFirstName(name, people) || exactCatalogPerson();
  }

  function applyCatalog(person) {
    if (!person) return;
    setName(person.name);
    setNamePick(-1);
    onAssign(cluster.id, person, category);
  }

  function submitName() {
    const typed = name.trim();
    if (!typed || saving) return;
    const pickedPerson = catalogPerson(true);
    if (pickedPerson) applyCatalog(pickedPerson);
    else onName(cluster.id, typed, category);
  }

  function acceptPersonDrop(event) {
    if (saving || !isPersonDrag(event.dataTransfer)) return false;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    return true;
  }

  function onPersonDragEnter(event) {
    if (!acceptPersonDrop(event)) return;
    dragDepth.current += 1;
    setDropOver(true);
  }

  function onPersonDragLeave() {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDropOver(false);
  }

  function onPersonDrop(event) {
    acceptPersonDrop(event);
    dragDepth.current = 0;
    setDropOver(false);
    const dragged = personFromDataTransfer(event.dataTransfer);
    if (!dragged) return;
    const person = people.find((p) => Number(p.id) === Number(dragged.id));
    if (person) onAssign(cluster.id, person, category);
  }

  const lookupProps = {
    clusterId: cluster.id,
    faceIds: (cluster.faces || []).map((f) => f.id),
    available: lookupOk,
    disabled: saving,
    onName: (suggested) => {
      if (suggested === "") {
        setName("");
        setNamePick(-1);
        return;
      }
      setName((cur) => (cur.trim() ? cur : suggested));
      setNamePick(-1);
    },
    onApplyExisting: (personId) => {
      const person = people.find((p) => p.id === personId);
      if (person) onAssign(cluster.id, person, category);
    },
    onConfirm: (hit) => onName(cluster.id, hit.name, category),
  };

  return (
    <div
      ref={cardRef}
      data-cluster-id={cluster.id}
      className={`card cluster${active ? " active" : ""}${saveError ? " has-save-error" : ""}`}
      onPointerDown={onActivate}
    >
      <div className="cluster-faces">
        <div className="cluster-kicker">
          <strong>
            {cluster.faces?.length && cluster.face_count > cluster.faces.length
              ? `${cluster.faces.length} of ${cluster.face_count} faces`
              : `${cluster.face_count} face${cluster.face_count === 1 ? "" : "s"}`}
          </strong>
          <span className="hint">#{cluster.id}</span>
          <span className="hint">{groupWhen(cluster)}</span>
          <FamousLookup variant="launch" {...lookupProps} />
        </div>
        <p className="hint cluster-select-hint">
          {cluster.faces?.length && cluster.face_count > cluster.faces.length
            ? "Naming applies only to the faces below, not the rest of this group."
            : canSplit
              ? "Click a face to see the whole photo. Mark any face that is not this person."
              : "Click the face to see the whole photo."}
        </p>
        <div className="crops" role={canSplit ? "group" : undefined} aria-label={canSplit ? "Faces in this group" : undefined}>
          {cluster.faces.map((f) => {
            const selected = picked.includes(f.id);
            const photoTo = f.photo_id ? `/photos/${f.photo_id}` : null;
            return (
              <div key={f.id} data-face-id={f.id} className={`crop ${selected ? "picked" : ""}`}>
                {near && photoTo ? (
                  <Link
                    className="crop-photo"
                    to={photoTo}
                    state={{ fullscreen: true, from: "/to-name" }}
                    onClick={() => onOpenPhoto?.(f)}
                    {...tip("Open the whole photo. Original stays on the NAS.")}
                  >
                    <img src={f.crop_url} alt="" decoding="async" />
                  </Link>
                ) : near ? (
                  <img src={f.crop_url} alt="" decoding="async" />
                ) : (
                  <span className="crop-ph" />
                )}
                {canSplit ? (
                  <button
                    type="button"
                    className={`crop-mark ${selected ? "on" : ""}`}
                    onPointerDown={(e) => e.stopPropagation()}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      toggle(f.id);
                    }}
                    {...tip(
                      selected
                        ? "This face will leave the group."
                        : "Mark if this face is someone else.",
                    )}
                  >
                    {selected ? "Someone else" : "Not this person"}
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
        {canSplit ? (
          <button
            type="button"
            className="secondary"
            disabled={!near || !picked.length}
            onClick={() => api.splitCluster(cluster.id, picked).then(onChange)}
            {...tip("Take the faces you clicked out of this group so you can name them separately.")}
          >
            {picked.length === 1
              ? "Name this face separately"
              : picked.length
                ? `Name these ${picked.length} separately`
                : "Name selected faces separately"}
          </button>
        ) : null}
      </div>

      <div
        className={`cluster-actions${dropOver ? " drop-over" : ""}`}
        onDragEnter={onPersonDragEnter}
        onDragOver={acceptPersonDrop}
        onDragLeave={onPersonDragLeave}
        onDrop={onPersonDrop}
      >
        {saveError ? (
          <p className="error save-error" role="alert">
            {saveError}
          </p>
        ) : null}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (canSave) submitName();
          }}
        >
          <label className="cluster-label" htmlFor={`name-${cluster.id}`}>
            Name this person
          </label>
          <div className="row">
            <input
              id={`name-${cluster.id}`}
              className="grow"
              placeholder={dropOver ? "Drop a name here" : "Type their name"}
              autoComplete="off"
              value={name}
              disabled={saving}
              aria-autocomplete="list"
              aria-expanded={catalogHits.length > 0}
              onChange={(e) => {
                setName(e.target.value);
                setNamePick(-1);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" && catalogHits.length) {
                  e.preventDefault();
                  setNamePick((cur) => (cur < 0 ? 0 : Math.min(catalogHits.length - 1, cur + 1)));
                  return;
                }
                if (e.key === "ArrowUp" && catalogHits.length) {
                  e.preventDefault();
                  setNamePick((cur) => (cur <= 0 ? 0 : cur - 1));
                  return;
                }
                if (e.key === "Tab" && catalogHits.length) {
                  const person = catalogPerson(true) || catalogHits[0];
                  if (person) {
                    e.preventDefault();
                    setName(person.name);
                    setNamePick(-1);
                  }
                  return;
                }
                if (e.key === "Escape" && catalogHits.length) {
                  e.preventDefault();
                  setNamePick(-1);
                }
              }}
            />
            <NameSuggest
              query={name}
              people={people}
              activeIndex={namePick}
              onPick={applyCatalog}
            />
            <button
              type="submit"
              className="secondary"
              disabled={!canSave}
              {...tip(
                highlightedCatalog
                  ? `Use ${highlightedCatalog.name} from the catalog for this whole group.`
                  : "Give this whole group one new name. You will not have to name each photo.",
              )}
            >
              <svg viewBox="0 0 16 16" aria-hidden="true">
                <path fill="currentColor" d="M6.2 11.4 2.8 8l1.1-1.1 2.3 2.3 5.9-5.9L13.2 4z" />
              </svg>
              {saving ? "Saving…" : "Save this name"}
            </button>
          </div>
          <div className="person-chips cluster-cats" role="group" aria-label="Family, work, or other">
            {CATEGORIES.map((c) => (
              <button
                type="button"
                key={c.id}
                className={`person-chip ${category === c.id ? "active" : ""}`}
                aria-pressed={category === c.id}
                onClick={() => onCategory(category === c.id ? "" : c.id)}
                {...tip("Sort this person as family, work, or other in Faces in DB View.")}
              >
                {c.label}
              </button>
            ))}
          </div>
        </form>

        {near ? <FamousLookup variant="results" {...lookupProps} /> : null}

        <div className="cluster-fixed">
          <button
            type="button"
            className="secondary"
            onClick={() => onUnknown(cluster.id, category)}
            {...tip("This is a real person, but you do not know the name yet. They are stored in the database and appear under Faces in DB View.")}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M8 1.6A6.4 6.4 0 1 0 14.4 8 6.41 6.41 0 0 0 8 1.6Zm0 2.3A2.05 2.05 0 1 1 6 6a2.05 2.05 0 0 1 2-2.1ZM8 13a5 5 0 0 1-3.7-1.6A4.6 4.6 0 0 1 8 9.6a4.6 4.6 0 0 1 3.7 1.8A5 5 0 0 1 8 13Z"
              />
            </svg>
            Unknown name of person
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => onJunk(cluster.id)}
            {...tip("This is a statue, painting, or other object. It will stay hidden, and similar faces will be ignored too.")}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M4.2 3.1 3.1 4.2 6.9 8l-3.8 3.8 1.1 1.1L8 9.1l3.8 3.8 1.1-1.1L9.1 8l3.8-3.8-1.1-1.1L8 6.9 4.2 3.1Z"
              />
            </svg>
            Not a person
          </button>
        </div>

        {saving ? <p className="hint">Saving that name…</p> : null}
      </div>
    </div>
  );
}
