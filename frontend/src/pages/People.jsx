import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";
import ViewSwitch from "../components/ViewSwitch.jsx";
import { loadCachedPeople, patchCachedPerson, saveCachedPeople } from "../peopleCache.js";

const IGNORE_KEY = "photosort-merge-ignore";
const CATEGORIES = [
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

function categoryOf(person) {
  return CATEGORIES.some((c) => c.id === person?.category) ? person.category : "";
}

function loadIgnored() {
  try {
    const raw = JSON.parse(sessionStorage.getItem(IGNORE_KEY) || "[]");
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

function pairKey(a, b) {
  return [a, b].sort((x, y) => x - y).join("-");
}

function coverOf(people, id) {
  return people.find((p) => p.id === id)?.cover_url;
}

function keepName(person) {
  if (!person?.unknown_name) return person?.name;
  return null;
}

function ChipCount({ n, known }) {
  if (!known) return null;
  return <span className="hint"> · {n}</span>;
}

export default function People() {
  const [params, setParams] = useSearchParams();
  const folder = (params.get("folder") || "").trim();
  const groupParam = (params.get("group") || "").trim();
  const group = groupParam || "family";
  const [people, setPeople] = useState(() => loadCachedPeople(folder));
  const [folders, setFolders] = useState([]);
  const [suggestions, setSuggestions] = useState([]);
  const [ready, setReady] = useState(() => loadCachedPeople(folder).length > 0);
  const [ignored, setIgnored] = useState(() => loadIgnored());
  const [err, setErr] = useState("");
  const [coverMenu, setCoverMenu] = useState(null);
  const countsKnown = ready || people.length > 0;

  useEffect(() => {
    let cancelled = false;
    const cached = loadCachedPeople(folder);
    if (cached.length) {
      setPeople(cached);
      setReady(true);
    } else {
      setReady(false);
    }
    setErr("");
    api
      .people(folder || undefined, { lite: 1 })
      .then((listed) => {
        if (cancelled) return;
        const items = listed.items || [];
        setPeople(items);
        saveCachedPeople(folder, items);
        if (listed.folders?.length) setFolders(listed.folders);
        setReady(true);
      })
      .catch((ex) => {
        if (!cancelled) {
          setReady(true);
          setErr(ex.message || "Could not load people.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [folder]);

  useEffect(() => {
    if (folder) loadFolders();
  }, [folder]);

  function loadFolders() {
    if (folders.length) return;
    api
      .peopleFolders()
      .then((listed) => setFolders(listed.items || []))
      .catch(() => {});
  }

  function loadMerges() {
    if (suggestions.length) return;
    api
      .mergeSuggestions()
      .then((listed) => setSuggestions(listed.items || []))
      .catch(() => setSuggestions([]));
  }

  function ignorePair(a, b) {
    const next = new Set(ignored);
    next.add(pairKey(a, b));
    setIgnored(next);
    sessionStorage.setItem(IGNORE_KEY, JSON.stringify([...next]));
  }

  function setGroup(next) {
    const cur = new URLSearchParams(params);
    if (!next || next === "family") cur.delete("group");
    else cur.set("group", next);
    setParams(cur, { replace: true });
  }

  function peopleHref(nextFolder) {
    const cur = new URLSearchParams();
    if (nextFolder) cur.set("folder", nextFolder);
    if (group && group !== "family") cur.set("group", group);
    const qs = cur.toString();
    return qs ? `/people?${qs}` : "/people";
  }

  function setCover(id, fields) {
    setPeople((cur) => cur.map((p) => (p.id === id ? { ...p, ...fields } : p)));
    patchCachedPerson(id, fields, folder);
  }

  function openCoverMenu(event, person) {
    if (!person?.cover_url) return;
    event.preventDefault();
    event.stopPropagation();
    setCoverMenu({ x: event.clientX, y: event.clientY, person });
  }

  function setCategory(id, category) {
    const value = category || "";
    setPeople((cur) => cur.map((p) => (p.id === id ? { ...p, category: value } : p)));
    patchCachedPerson(id, { category: value }, folder);
    api.patchPerson(id, { category: value }).catch(() => {
      api.people(folder || undefined, { lite: 1 }).then((listed) => {
        const items = listed.items || [];
        setPeople(items);
        saveCachedPeople(folder, items);
      });
    });
  }

  const grouped = useMemo(() => {
    if (group === "all") return people;
    if (group === "unset") return people.filter((p) => !categoryOf(p));
    return people.filter((p) => categoryOf(p) === group);
  }, [people, group]);
  const named = useMemo(() => grouped.filter((p) => !p.unknown_name), [grouped]);
  const unnamed = useMemo(() => grouped.filter((p) => p.unknown_name), [grouped]);
  const inFolder = useMemo(() => new Set(people.map((p) => p.id)), [people]);
  const visible = suggestions.filter((s) => {
    if (ignored.has(pairKey(s.person_a.id, s.person_b.id))) return false;
    if (!folder) return true;
    return inFolder.has(s.person_a.id) && inFolder.has(s.person_b.id);
  });

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Identified faces in the database</p>
          <h1>Faces in DB View</h1>
          <p className="lede">
            {folder
              ? `Identified faces stored in the database who appear in ${folder}. Counts are for this folder only.`
              : "Identified faces stored in the local database — people the catalog already knows. This is not the photo album."}{" "}
            Mark someone Family, Work, or Other to sort this list. Folder View and View by person show
            the original pictures. Click a face to open that person. Only join two cards if you are sure
            they are the same person at different ages.
          </p>
        </div>
        <ViewSwitch />
      </div>

      <div className="folder-filter">
        <p className="view-switch-label">Category</p>
        <div className="person-chips" role="tablist" aria-label="Category">
          <button
            type="button"
            className={`person-chip ${group === "all" ? "active" : ""}`}
            aria-current={group === "all" ? "page" : undefined}
            onClick={() => setGroup("all")}
            {...tip("Show every person in Faces in DB View.")}
          >
            All
          </button>
          {CATEGORIES.map((c) => (
            <button
              type="button"
              key={c.id}
              className={`person-chip ${group === c.id ? "active" : ""}`}
              aria-current={group === c.id ? "page" : undefined}
              onClick={() => setGroup(c.id)}
              {...tip(`Show people marked ${c.label}.`)}
            >
              {c.label}
              <ChipCount n={people.filter((p) => categoryOf(p) === c.id).length} known={countsKnown} />
            </button>
          ))}
          <button
            type="button"
            className={`person-chip ${group === "unset" ? "active" : ""}`}
            aria-current={group === "unset" ? "page" : undefined}
            onClick={() => setGroup("unset")}
            {...tip("Show people not marked Family, Work, or Other yet.")}
          >
            Not set
            <ChipCount n={people.filter((p) => !categoryOf(p)).length} known={countsKnown} />
          </button>
        </div>
      </div>

      {folders.length ? (
        <details
          className="folder-filter folder-filter-fold"
          open={Boolean(folder) || undefined}
          onToggle={(e) => {
            if (e.currentTarget.open) loadFolders();
          }}
        >
          <summary className="view-switch-label" {...tip("Optional. Open to show people from one album folder.")}>
            Folder
            <span className="hint">{folder ? ` · ${folder}` : " · all folders"}</span>
          </summary>
          <div className="person-chips" role="navigation" aria-label="Folder">
            <Link
              to={peopleHref("")}
              className={`person-chip ${!folder ? "active" : ""}`}
              aria-current={!folder ? "page" : undefined}
              {...tip("Show every identified face stored in the database.")}
            >
              All folders
            </Link>
            {folders.map((f) => (
              <Link
                key={f.folder}
                to={peopleHref(f.folder)}
                className={`person-chip ${folder === f.folder ? "active" : ""}`}
                aria-current={folder === f.folder ? "page" : undefined}
                {...tip(`Show identified faces from the database who appear in ${f.folder}.`)}
              >
                {f.folder}
              </Link>
            ))}
          </div>
        </details>
      ) : null}

      {named.length ? (
        <section className="folder-block">
          <h2>
            Named
            <span className="hint"> · {named.length} stored in the database</span>
          </h2>
          <div className="people-grid">
            {named.map((p) => (
              <PersonCard
                key={p.id}
                person={p}
                onCategory={setCategory}
                onCoverMenu={openCoverMenu}
              />
            ))}
          </div>
        </section>
      ) : null}

      {unnamed.length ? (
        <section className="folder-block">
          <h2>
            Name unknown
            <span className="hint"> · {unnamed.length}</span>
          </h2>
          <p className="hint" style={{ marginTop: -4, marginBottom: 12 }}>
            Identified as people and stored in the database, without a name yet. Open one to rename them.
          </p>
          <div className="people-grid">
            {unnamed.map((p) => (
              <PersonCard
                key={p.id}
                person={p}
                onCategory={setCategory}
                onCoverMenu={openCoverMenu}
              />
            ))}
          </div>
        </section>
      ) : null}

      {ready && people.length ? (
        <details className="merge-fold" onToggle={(e) => e.currentTarget.open && loadMerges()}>
          <summary>
            {suggestions.length
              ? `${visible.length} possible join${visible.length === 1 ? "" : "s"}`
              : "Possible joins"}
            <span className="hint"> — only if a child and adult are the same person</span>
          </summary>
          {visible.map((s) => (
            <MergeCard
              key={pairKey(s.person_a.id, s.person_b.id)}
              suggestion={s}
              people={people}
              onJoin={() => {
                api.people(folder || undefined).then((listed) => {
                  setPeople(listed.items || []);
                  if (listed.folders) setFolders(listed.folders);
                });
                api.mergeSuggestions().then((listed) => setSuggestions(listed.items || []));
              }}
              onIgnore={() => ignorePair(s.person_a.id, s.person_b.id)}
            />
          ))}
        </details>
      ) : null}

      {err ? (
        <p className="error">
          {err}{" "}
          <button type="button" className="ghost" onClick={() => window.location.reload()}>
            Try again
          </button>
        </p>
      ) : null}
      {!ready && !people.length ? <p className="hint">Loading people…</p> : null}
      {coverMenu ? (
        <CoverMenu
          menu={coverMenu}
          onClose={() => setCoverMenu(null)}
          onPicked={(person) => {
            setCover(person.id, {
              cover_url: person.cover_url,
              cover_face_id: person.cover_face_id,
            });
            setCoverMenu(null);
          }}
        />
      ) : null}
      {ready && !people.length && !err ? (
        <div className="card empty">
          {folder
            ? `No identified faces from ${folder} are in the database yet. Name someone on To name, or pick another folder.`
            : "No identified faces in the database yet. Name someone on To name and they are stored here."}
        </div>
      ) : null}
      {ready && people.length && !grouped.length ? (
        <div className="card empty">No people in this category yet. Mark someone Family, Work, or Other on their card.</div>
      ) : null}
    </div>
  );
}

function PersonCard({ person, onCategory, onCoverMenu }) {
  const category = categoryOf(person);
  return (
    <div className="card person-card">
      <Link className="person-card-link" to={`/people/${person.id}`}>
        <div
          className="person-head"
          onContextMenu={(event) => onCoverMenu(event, person)}
        >
          {person.cover_url ? (
            <img src={person.cover_url} alt="" loading="lazy" decoding="async" />
          ) : (
            <div className="person-head-empty" />
          )}
        </div>
        <h3>{person.unknown_name ? "Name unknown" : person.name}</h3>
        {person.nickname ? <div className="person-nick">Also {person.nickname}</div> : null}
        <div className="hint">
          {person.face_count || 0} photo{(person.face_count || 0) === 1 ? "" : "s"}
          {person.first_seen
            ? ` · ${String(person.first_seen).slice(0, 4)}${person.last_seen ? `–${String(person.last_seen).slice(0, 4)}` : ""}`
            : ""}
        </div>
      </Link>
      <label className="person-cat">
        <span className="visually-hidden">Category</span>
        <select
          value={category}
          onChange={(e) => onCategory(person.id, e.target.value)}
          {...tip("Family, work, or other. This only sorts Faces in DB View.")}
        >
          <option value="">Not set</option>
          {CATEGORIES.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function MergeCard({ suggestion, people, onJoin, onIgnore }) {
  const a = suggestion.person_a;
  const b = suggestion.person_b;
  const keepA = keepName(people.find((p) => p.id === a.id) || a);
  const keepB = keepName(people.find((p) => p.id === b.id) || b);
  const joins = [];
  if (keepA) joins.push({ target: a, source: b, label: `Join as ${keepA}` });
  if (keepB && keepB !== keepA) joins.push({ target: b, source: a, label: `Join as ${keepB}` });
  if (!joins.length) {
    joins.push({ target: a, source: b, label: "Join these two" });
  }

  return (
    <div className="card merge">
      <div className="merge-pair">
        <Link className="merge-face" to={`/people/${a.id}`}>
          {coverOf(people, a.id) ? <img src={coverOf(people, a.id)} alt="" /> : <span className="person-picker-gap" />}
          <span>{keepA || "Name unknown"}</span>
        </Link>
        <span className="hint" aria-hidden="true">
          ?
        </span>
        <Link className="merge-face" to={`/people/${b.id}`}>
          {coverOf(people, b.id) ? <img src={coverOf(people, b.id)} alt="" /> : <span className="person-picker-gap" />}
          <span>{keepB || "Name unknown"}</span>
        </Link>
      </div>
      <p className="hint">Same person at two ages? Compare the faces, then join or skip.</p>
      <div className="row" style={{ marginTop: 8 }}>
        {joins.map((j) => (
          <button
            key={j.label}
            className="secondary"
            onClick={() => api.mergePerson(j.target.id, j.source.id).then(onJoin)}
            {...tip(`Keep the name ${j.target.name} and add the other photos to them.`)}
          >
            {j.label}
          </button>
        ))}
        <button type="button" className="ghost" onClick={onIgnore} {...tip("Hide this pair. They stay two people.")}>
          Not the same person
        </button>
      </div>
    </div>
  );
}

const MENU_PAD = 8;

function CoverMenu({ menu, onClose, onPicked }) {
  const box = useRef(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    function insideMenu(event) {
      if (!box.current) return false;
      if (event?.target && box.current.contains(event.target)) return true;
      return false;
    }
    function hide(event) {
      if (event?.type === "keydown") {
        if (event.key !== "Escape") return;
        onClose();
        return;
      }
      if (insideMenu(event)) return;
      onClose();
    }
    window.addEventListener("pointerdown", hide, true);
    window.addEventListener("keydown", hide);
    window.addEventListener("scroll", hide, true);
    return () => {
      window.removeEventListener("pointerdown", hide, true);
      window.removeEventListener("keydown", hide);
      window.removeEventListener("scroll", hide, true);
    };
  }, [onClose]);

  useLayoutEffect(() => {
    if (!box.current) return;
    const r = box.current.getBoundingClientRect();
    let left = menu.x;
    let top = menu.y;
    if (left + r.width > window.innerWidth - MENU_PAD) left = window.innerWidth - r.width - MENU_PAD;
    if (top + r.height > window.innerHeight - MENU_PAD) top = window.innerHeight - r.height - MENU_PAD;
    if (left < MENU_PAD) left = MENU_PAD;
    if (top < MENU_PAD) top = MENU_PAD;
    box.current.style.left = `${left}px`;
    box.current.style.top = `${top}px`;
  }, [menu]);

  async function findBetter() {
    if (busy) return;
    setBusy(true);
    try {
      const next = await api.nextPersonCover(menu.person.id);
      onPicked(next);
    } catch (ex) {
      window.alert(ex.message || "Could not find another photo.");
      setBusy(false);
    }
  }

  return (
    <div
      ref={box}
      className="photo-menu"
      role="menu"
      aria-label="Person cover"
      onPointerDown={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <button type="button" role="menuitem" disabled={busy} onClick={findBetter}>
        {busy ? "Finding…" : "Find a better photo"}
      </button>
    </div>
  );
}
