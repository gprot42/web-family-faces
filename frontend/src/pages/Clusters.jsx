import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";
import { groupWhen } from "../ages.js";
import { matchPeople, uniqueFirstName } from "../nameSuggest.js";
import FamousLookup from "../components/FamousLookup.jsx";
import JobGauge from "../components/JobGauge.jsx";
import NameSuggest from "../components/NameSuggest.jsx";
import PersonPicker from "../components/PersonPicker.jsx";

function clusterKey(cluster) {
  const ids = cluster.face_ids;
  if (ids?.length) return `f-${Math.min(...ids)}`;
  return `c-${cluster.id}`;
}

function useNearViewport(startNear) {
  const ref = useRef(null);
  const [near, setNear] = useState(Boolean(startNear));
  useEffect(() => {
    const node = ref.current;
    if (!node) return undefined;
    const io = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), {
      rootMargin: "200px 0px",
    });
    io.observe(node);
    return () => io.disconnect();
  }, []);
  return [ref, near];
}

const CATEGORIES = [
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

const POS_KEY = "photosort-to-name-pos";

function readToNamePos() {
  try {
    return JSON.parse(sessionStorage.getItem(POS_KEY) || "null");
  } catch {
    return null;
  }
}

function writeToNamePos(pos) {
  try {
    sessionStorage.setItem(POS_KEY, JSON.stringify(pos));
  } catch {
    /* ignore quota */
  }
}

export default function Clusters({ onChange }) {
  const [items, setItems] = useState([]);
  const [people, setPeople] = useState([]);
  const [err, setErr] = useState("");
  const [savingId, setSavingId] = useState(null);
  const [saved, setSaved] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showCats, setShowCats] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [catById, setCatById] = useState({});
  const groupsRef = useRef(null);
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
    const c = await api.clusters();
    setItems(c.items || []);
    api
      .people(undefined, { lite: true })
      .then((p) => setPeople(p.items || []))
      .catch(() => {});
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

  useEffect(() => {
    if (loading || restoredPos.current) return;
    const el = groupsRef.current;
    const pos = readToNamePos();
    if (!el || !pos) return;
    restoredPos.current = true;
    if (pos.activeId && items.some((item) => item.id === pos.activeId)) {
      setActiveId(pos.activeId);
    }
    const apply = () => {
      const card = pos.clusterId
        ? el.querySelector(`[data-cluster-id="${pos.clusterId}"]`)
        : null;
      if (card) card.scrollIntoView({ block: "start" });
      else el.scrollTop = Number(pos.scrollTop) || 0;
    };
    requestAnimationFrame(() => requestAnimationFrame(apply));
  }, [loading, items]);

  function faceIdsOf(id) {
    const cluster = items.find((item) => item.id === id);
    if (!cluster) return [];
    if (cluster.face_ids?.length) return cluster.face_ids;
    if (cluster.faces?.length) return cluster.faces.map((f) => f.id);
    return [];
  }

  async function finishSave(id, name, result, faceCount) {
    setSaved({
      name,
      faces: result.assigned || faceCount || 0,
      also: result.also_matched || 0,
    });
    await refresh();
    onChange?.();
  }

  async function nameCluster(id, name, category) {
    setErr("");
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setItems((cur) => cur.filter((c) => c.id !== id));
    setSaved({ name, faces: namedCount, also: 0 });
    onChange?.({
      unknown_clusters: -1,
      faces_unknown: -namedCount,
      people: 1,
      people_named: 1,
    });
    try {
      const result = await api.nameCluster(id, name, faceIds, category);
      if (!result.assigned) {
        throw new Error("That group was regrouped. Save this name again.");
      }
      setSaved({
        name: result.person?.name || name,
        faces: result.assigned || faceCount,
        also: result.also_matched || 0,
      });
    } catch (ex) {
      setErr(ex.message);
      await refresh();
    }
  }

  async function markJunk(id) {
    setErr("");
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setItems((cur) => cur.filter((c) => c.id !== id));
    setSaved({ name: "Not a person", faces: namedCount, also: 0 });
    onChange?.({ unknown_clusters: -1, faces_unknown: -namedCount });
    try {
      const result = await api.junkCluster(id, faceIds);
      if (!result.cleared) {
        throw new Error("That group was regrouped. Click Not a person again.");
      }
    } catch (ex) {
      setErr(ex.message);
      await refresh();
    }
  }

  async function markUnknown(id, category) {
    setErr("");
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
        throw new Error("That group was regrouped. Click Unknown again.");
      }
      api
        .people(undefined, { lite: true })
        .then((p) => setPeople(p.items || []))
        .catch(() => {});
    } catch (ex) {
      setErr(ex.message);
      await refresh();
    }
  }

  async function assignToPerson(id, person, category) {
    setErr("");
    const faceIds = faceIdsOf(id);
    const namedCount = faceIds.length;
    setItems((cur) => cur.filter((c) => c.id !== id));
    setSaved({ name: person.name, faces: namedCount, also: 0 });
    onChange?.({ unknown_clusters: -1, faces_unknown: -namedCount });
    try {
      const result = await api.assignCluster(id, person.id, faceIds, category);
      if (!result.assigned) {
        throw new Error("That group was regrouped. Click the name again.");
      }
      if (result.also_matched) {
        setSaved({
          name: person.name,
          faces: result.assigned || faceCount,
          also: result.also_matched,
        });
      }
    } catch (ex) {
      setErr(ex.message);
      await refresh();
    }
  }

  const identifying = Boolean(job && (job.status === "running" || job.status === "queued"));
  const identifyPaused = Boolean(job && job.status === "paused");

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
      <div className={`to-name-layout${people.some((p) => !p.unknown_name) && items.length ? " has-named" : ""}`}>
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
          {err ? <p className="error">{err}</p> : null}
          {identifyNote ? (
            <div className="save-note" role="status">
              {identifyNote}. Groups named from the catalog also appear under Check names.
            </div>
          ) : null}
          {saved ? (
            <div className="save-note" role="status" aria-live="polite">
              Saved {saved.name}. {saved.faces} face{saved.faces === 1 ? "" : "s"} in that group
              {saved.also ? ` · ${saved.also} more matched` : ""}.
              {items.length ? " Next group is below." : " Nothing left to name here."}
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
              eager={i === 0}
              active={c.id === activeId}
              category={catById[c.id] || ""}
              onCategory={(next) => setCatById((cur) => ({ ...cur, [c.id]: next }))}
              onActivate={() => setActiveId(c.id)}
              onName={nameCluster}
              onAssign={assignToPerson}
              onUnknown={markUnknown}
              onJunk={markJunk}
              onOpenPhoto={() => rememberPos({ clusterId: c.id, activeId: c.id })}
              onChange={refresh}
              lookupOk={lookupOk}
            />
          ))}
        </div>
        {items.length && people.some((p) => !p.unknown_name) ? (
          <aside className="to-name-named">
            <div className="cluster-or">or click someone already named</div>
            <p className="hint">
              Applies to the highlighted group
              {items.find((c) => c.id === activeId)
                ? ` · ${items.find((c) => c.id === activeId).face_count} face${items.find((c) => c.id === activeId).face_count === 1 ? "" : "s"}`
                : ""}
              .
            </p>
            <PersonPicker
              people={people}
              showCategoryFilter
              categoryFilter={showCats}
              onCategoryFilter={setShowCats}
              hint="This whole group gets that name."
              onPick={(p) => {
                const target = items.find((c) => c.id === activeId) || items[0];
                if (target) assignToPerson(target.id, p, catById[target.id] || "");
              }}
            />
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
  lookupOk = true,
}) {
  const [name, setName] = useState("");
  const [namePick, setNamePick] = useState(-1);
  const [picked, setPicked] = useState([]);
  const [cardRef, near] = useNearViewport(eager);
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
      className={`card cluster${active ? " active" : ""}`}
      onPointerDown={onActivate}
    >
      <div className="cluster-faces">
        <div className="cluster-kicker">
          <strong>
            {cluster.faces?.length && cluster.face_count > cluster.faces.length
              ? `${cluster.faces.length} of ${cluster.face_count} faces`
              : `${cluster.face_count} face${cluster.face_count === 1 ? "" : "s"}`}
          </strong>
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
              <div key={f.id} className={`crop ${selected ? "picked" : ""}`}>
                {near && photoTo ? (
                  <Link
                    className="crop-photo"
                    to={photoTo}
                    state={{ fullscreen: true, from: "/to-name" }}
                    onClick={() => onOpenPhoto?.()}
                    {...tip("Open the whole photo. Original stays on the NAS.")}
                  >
                    <img src={f.crop_url} alt="" decoding="async" />
                  </Link>
                ) : near ? (
                  <img src={f.crop_url} alt="" decoding="async" />
                ) : (
                  <span className="crop-ph" />
                )}
                {near && canSplit ? (
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
        {near && canSplit ? (
          <button
            type="button"
            className="secondary"
            disabled={!picked.length}
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

      <div className="cluster-actions">
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
              placeholder="Type their name"
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
