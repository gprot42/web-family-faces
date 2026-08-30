import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { beginPlay } from "../play.js";
import { tip } from "../tip.js";
import { faceWhen } from "../ages.js";
import ViewSwitch from "../components/ViewSwitch.jsx";
import PersonPicker from "../components/PersonPicker.jsx";
import FamousLookup from "../components/FamousLookup.jsx";
import NameSuggest from "../components/NameSuggest.jsx";
import { PhotoTagRow } from "../components/PhotoTags.jsx";
import { emitCatalogChange, PHOTO_CHANGE_EVENT, rememberPhotoRotation, showPhotoMenu } from "../photoMenu.js";
import { patchCachedPerson } from "../peopleCache.js";
import { clearPersonPos, personShotHash, readPersonPos, writePersonPos } from "../albumPos.js";
import { completeUniqueFirstName, matchPeople, selectCompletedSuffix, uniqueCatalogPerson } from "../nameSuggest.js";

const PERSON_CATEGORIES = [
  { id: "", label: "Not set" },
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

export default function PersonDetail() {
  const { id } = useParams();
  const loc = useLocation();
  const nav = useNavigate();
  const [person, setPerson] = useState(null);
  const [people, setPeople] = useState([]);
  const [name, setName] = useState("");
  const [nickname, setNickname] = useState("");
  const [notes, setNotes] = useState("");
  const [notesState, setNotesState] = useState("idle");
  const [nickState, setNickState] = useState("idle");
  const [saveState, setSaveState] = useState("idle");
  const [namePick, setNamePick] = useState(-1);
  const [err, setErr] = useState("");
  const loadGen = useRef(0);

  async function load() {
    const gen = ++loadGen.current;
    const p = await api.person(id);
    if (gen !== loadGen.current) return p;
    setPerson(p);
    setName(p.unknown_name ? "" : p.name);
    setNamePick(-1);
    setNickname(p.nickname || "");
    setNotes(p.notes || "");
    setNotesState("idle");
    setNickState("idle");
    api
      .people(undefined, { lite: 1 })
      .then((all) => {
        if (gen !== loadGen.current) return;
        setPeople((all.items || []).filter((x) => String(x.id) !== String(id)));
      })
      .catch(() => {});
    return p;
  }

  function applyPerson(next, fallbackName) {
    setPerson((cur) => ({
      ...(cur || {}),
      ...next,
      shots: next.shots?.length ? next.shots : cur?.shots || [],
      name: next.name || fallbackName || cur?.name,
      nickname: next.nickname !== undefined ? next.nickname : cur?.nickname,
      unknown_name: next.unknown_name ?? false,
    }));
    if (next.name || fallbackName) setName(next.name || fallbackName);
    if (next.nickname !== undefined) setNickname(next.nickname || "");
  }

  function catalogHits(query = name) {
    return matchPeople(query, people, { excludeId: id });
  }

  function exactCatalogPerson(query = name) {
    const q = String(query || "").trim().toLowerCase();
    if (!q) return null;
    return (
      people.find((p) => !p.unknown_name && String(p.name || "").trim().toLowerCase() === q) || null
    );
  }

  function catalogPerson(query = name, { useHighlight = false } = {}) {
    const hits = catalogHits(query);
    if (useHighlight && namePick >= 0 && hits[namePick]) return hits[namePick];
    return uniqueCatalogPerson(query, people, { excludeId: id }) || exactCatalogPerson(query);
  }

  async function joinCatalog(target) {
    if (!target || String(target.id) === String(id) || saveState === "saving") return;
    setName(target.name);
    setNamePick(-1);
    setErr("");
    setSaveState("saving");
    try {
      await api.mergePerson(target.id, id);
      emitCatalogChange();
      nav(`/people/${target.id}`);
    } catch (ex) {
      setSaveState("idle");
      setErr(ex.message || "Could not join this with that person.");
      await load();
    }
  }

  async function saveName(event) {
    event?.preventDefault();
    event?.stopPropagation();
    const typed = event?.target && "value" in event.target ? event.target.value : name;
    const next = String(typed ?? name).trim();
    if (!next || saveState === "saving") return;
    const picked = catalogPerson(next, { useHighlight: namePick >= 0 });
    if (picked) {
      await joinCatalog(picked);
      return;
    }
    setErr("");
    setSaveState("saving");
    setPerson((cur) => (cur ? { ...cur, name: next, unknown_name: false } : cur));
    try {
      const updated = await api.patchPerson(id, { name: next });
      applyPerson(updated, next);
      setSaveState("saved");
    } catch (ex) {
      setSaveState("idle");
      setErr(ex.message || "Could not save the name.");
      await load();
    }
  }

  async function saveNickname(event) {
    event?.preventDefault();
    event?.stopPropagation();
    const next = nickname.trim();
    if (next === String(person?.nickname || "").trim() || nickState === "saving") return;
    setErr("");
    setNickState("saving");
    try {
      const updated = await api.patchPerson(id, { nickname: next });
      applyPerson(updated);
      setNickname(updated.nickname || "");
      setNickState("saved");
    } catch (ex) {
      setNickState("idle");
      setErr(ex.message || "Could not save the nickname.");
      await load();
    }
  }

  async function saveNotes(event) {
    event?.preventDefault();
    event?.stopPropagation();
    const next = notes.trim();
    if (next === String(person?.notes || "").trim() || notesState === "saving") return;
    setErr("");
    setNotesState("saving");
    try {
      const updated = await api.patchPerson(id, { notes: next });
      applyPerson(updated);
      setNotes(updated.notes || "");
      setNotesState("saved");
    } catch (ex) {
      setNotesState("idle");
      setErr(ex.message || "Could not save the note.");
      await load();
    }
  }

  function dropShot(face) {
    setPerson((cur) => {
      if (!cur) return cur;
      const faces = (cur.faces || []).filter((f) => f.id !== face.id && f.photo_id !== face.photo_id);
      const shots = (cur.shots || []).filter((f) => f.id !== face.id && f.photo_id !== face.photo_id);
      return { ...cur, faces, shots, face_count: shots.length };
    });
  }

  async function removeShot(face) {
    setErr("");
    dropShot(face);
    try {
      await api.unassignFace(face.id);
      emitCatalogChange();
    } catch (ex) {
      setErr(ex.message || "Could not remove this photo.");
      await load();
    }
  }

  async function setCategory(category) {
    const prev = person?.category || "";
    if (prev === category) return;
    setErr("");
    loadGen.current += 1;
    setPerson((cur) => (cur ? { ...cur, category } : cur));
    try {
      const updated = await api.patchPerson(id, { category });
      applyPerson(updated);
      patchCachedPerson(id, { category: updated.category || category || "" });
    } catch (ex) {
      setPerson((cur) => (cur ? { ...cur, category: prev } : cur));
      setErr(ex.message || "Could not save Family, Work, or Other.");
    }
  }

  useEffect(() => {
    load();
  }, [id]);

  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next?.id) return;
      setPerson((cur) => {
        if (!cur) return cur;
        if (next.hidden) {
          const shots = (cur.shots || []).filter((shot) => Number(shot.photo_id) !== Number(next.id));
          const faces = (cur.faces || []).filter((face) => Number(face.photo_id) !== Number(next.id));
          return { ...cur, shots, faces, face_count: shots.length };
        }
        const patchShot = (shot) => {
          if (Number(shot.photo_id) !== Number(next.id)) return shot;
          const rot = next.rotation != null ? Number(next.rotation) || 0 : shot.rotation;
          return {
            ...shot,
            rotation: rot,
            tags: next.tags !== undefined ? next.tags || [] : shot.tags,
            comment: next.comment !== undefined ? next.comment : shot.comment,
            crop_url: shot.crop_url
              ? String(shot.crop_url).replace(/([?&])r=\d+/, `$1r=${rot}`).replace(/([?&])v=\d+/, `$1v=${Date.now()}`)
              : shot.crop_url,
          };
        };
        return {
          ...cur,
          shots: (cur.shots || []).map(patchShot),
          faces: (cur.faces || []).map(patchShot),
        };
      });
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, []);

  const restoredFor = useRef("");
  const shots = person?.shots?.length ? person.shots : person?.faces || [];

  useEffect(() => {
    if (!person?.id || !shots.length) return undefined;
    const hash = String(loc.hash || window.location.hash || "").replace(/^#/, "");
    const fromHash = hash.match(/^photo-tile-(\d+)$/);
    const pos = readPersonPos();
    let photoId = 0;
    if (fromHash) photoId = Number(fromHash[1]);
    else if (Number(pos?.personId) === Number(person.id)) photoId = Number(pos.photoId) || 0;
    if (!photoId) return undefined;
    const key = `${person.id}:${photoId}`;
    if (restoredFor.current === key) return undefined;
    const run = () => {
      const tile = document.getElementById(personShotHash(photoId));
      if (!tile) return false;
      tile.scrollIntoView({ block: "center", inline: "nearest" });
      restoredFor.current = key;
      if (Number(pos?.personId) === Number(person.id)) clearPersonPos();
      return true;
    };
    if (run()) return undefined;
    const timer = window.setTimeout(run, 80);
    return () => window.clearTimeout(timer);
  }, [person?.id, shots.length, loc.hash]);

  if (!person) return <p className="hint">Loading…</p>;

  const cover = person.cover_url || shots[0]?.crop_url || shots[0]?.thumb_url;
  const nameHits = catalogHits();
  const highlightedCatalog = catalogPerson(name, { useHighlight: namePick >= 0 });

  return (
    <div className="person-page">
      <header className="person-sticky">
        <div className="person-sticky-bar">
          {cover ? (
            <img className="person-sticky-cover" src={cover} alt="" />
          ) : (
            <span className="person-sticky-cover empty" aria-hidden="true" />
          )}
          <div className="person-sticky-id">
            <h1>{person.name}</h1>
            <p className="lede">
              {person.nickname ? <>Also {person.nickname} · </> : null}
              {shots.length} photo{shots.length === 1 ? "" : "s"} with this name
            </p>
          </div>
          <ViewSwitch photoId={shots[0]?.photo_id} personId={person.id} />
        </div>
        <div className="row person-sticky-actions">
          <button
            type="button"
            className="secondary"
            disabled={!shots.length}
            onClick={() =>
              beginPlay(nav, shots, {
                kind: "person",
                title: person.unknown_name ? "Name unknown" : person.name,
                personId: person.id,
                from: `/people/${person.id}`,
              })
            }
            {...tip("Play this person's photos in date order, names on. Fullscreen.")}
          >
            Play person
          </button>
          {shots.length ? (
            <span className="person-download" role="group" aria-label="Download photos">
              <a
                className="btn secondary"
                href={`/api/people/${person.id}/photos.zip`}
                {...tip("Save a zip of these pictures. Uses the original when it is on disk, otherwise the local preview. Album files stay where they are.")}
              >
                Download photos
              </a>
              <a
                className="btn secondary"
                href={`/api/people/${person.id}/photos.zip?labels=1`}
                {...tip("Save a zip of copies with name tags drawn on. Originals are not changed.")}
              >
                With labels
              </a>
            </span>
          ) : (
            <button type="button" className="secondary" disabled>
              Download photos
            </button>
          )}
          <Link
            className="btn secondary"
            to={`/tree?person=${person.id}`}
            {...tip("Open this person on the Family tree page, centered among their relatives.")}
          >
            Show in family tree
          </Link>
        </div>
        <form className="row person-sticky-name" onSubmit={saveName}>
          <div className="person-name-field">
            <input
              className="grow"
              value={name}
              placeholder="Type their name"
              autoComplete="off"
              autoCorrect="off"
              spellCheck={false}
              aria-label="Person name"
              aria-autocomplete="list"
              aria-expanded={nameHits.length > 0}
              disabled={saveState === "saving"}
              onChange={(e) => {
                const value = e.target.value;
                const shrinking = value.length < String(name || "").length;
                const unique = shrinking ? null : completeUniqueFirstName(value, people, { excludeId: id });
                const next = unique ? unique.name : value;
                setName(next);
                setNamePick(-1);
                if (saveState === "saved") setSaveState("idle");
                if (unique) selectCompletedSuffix(e.target, value, next);
              }}
              onKeyDown={(e) => {
                const hits = catalogHits();
                if (e.key === "ArrowDown" && hits.length) {
                  e.preventDefault();
                  e.stopPropagation();
                  setNamePick((cur) => (cur < 0 ? 0 : Math.min(hits.length - 1, cur + 1)));
                  return;
                }
                if (e.key === "ArrowUp" && hits.length) {
                  e.preventDefault();
                  e.stopPropagation();
                  setNamePick((cur) => (cur <= 0 ? 0 : cur - 1));
                  return;
                }
                if (e.key === "Tab" && hits.length) {
                  const person = catalogPerson(name, { useHighlight: true }) || hits[0];
                  if (person) {
                    e.preventDefault();
                    e.stopPropagation();
                    setName(person.name);
                    setNamePick(-1);
                  }
                  return;
                }
                if (e.key === "Escape" && hits.length) {
                  e.preventDefault();
                  setNamePick(-1);
                  return;
                }
                const hitNum = Number(e.key);
                if (hitNum >= 1 && hitNum <= hits.length) {
                  e.preventDefault();
                  e.stopPropagation();
                  joinCatalog(hits[hitNum - 1]);
                  return;
                }
                if (e.key === "Enter") {
                  e.preventDefault();
                  e.stopPropagation();
                  saveName(e);
                }
              }}
            />
            <NameSuggest
              query={name}
              people={people}
              excludeId={id}
              activeIndex={namePick}
              onPick={joinCatalog}
            />
          </div>
          <button
            type="submit"
            className={saveState === "saved" ? "saved" : undefined}
            disabled={!name.trim() || saveState === "saving"}
            {...tip(
              highlightedCatalog
                ? `Join this with ${highlightedCatalog.name} from the catalog. Photo files are not edited.`
                : "Change the displayed name. If they are already in the catalog, this identity joins them. Photo files are not edited. Enter saves.",
            )}
          >
            {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : "Save name"}
          </button>
        </form>
        <form className="row person-sticky-name" onSubmit={saveNickname}>
          <input
            id="person-nickname"
            className="grow"
            value={nickname}
            aria-label="Nickname"
            placeholder="Nickname (optional)"
            disabled={nickState === "saving"}
            onChange={(e) => {
              setNickname(e.target.value);
              if (nickState === "saved") setNickState("idle");
            }}
            onBlur={saveNickname}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                e.stopPropagation();
                saveNickname(e);
              }
            }}
            {...tip("A nickname or familiar name. Search and name suggestions use this as well as the full name.")}
          />
          <button
            type="submit"
            className={nickState === "saved" ? "saved" : "secondary"}
            disabled={nickState === "saving" || nickname.trim() === String(person.nickname || "").trim()}
            {...tip("Save the nickname. Original photos are not changed.")}
          >
            {nickState === "saving" ? "Saving…" : nickState === "saved" ? "Saved" : "Save nickname"}
          </button>
        </form>
        <div className="person-chips" role="group" aria-label="Category">
          {PERSON_CATEGORIES.map((c) => {
            const on = (person.category || "") === c.id;
            return (
              <button
                type="button"
                key={c.id || "unset"}
                className={`person-chip ${on ? "active" : ""}`}
                aria-pressed={on}
                onClick={() => setCategory(c.id)}
                {...tip("Sort this person as family, work, or other in Faces in DB View.")}
              >
                {c.label}
              </button>
            );
          })}
        </div>
        {err ? <p className="error">{err}</p> : null}
      </header>
      <form className="person-notes" onSubmit={saveNotes}>
        <label className="cluster-label" htmlFor="person-notes">
          Notes
        </label>
        <textarea
          id="person-notes"
          rows={3}
          maxLength={4000}
          value={notes}
          placeholder="A note about this person. It stays in the catalog, not on the photos."
          onChange={(e) => {
            setNotes(e.target.value);
            if (notesState === "saved") setNotesState("idle");
          }}
          onBlur={saveNotes}
          {...tip("Saved with this person in the catalog. Original photos are not changed.")}
        />
        <button
          type="submit"
          className={notesState === "saved" ? "saved" : "secondary"}
          disabled={notesState === "saving" || notes.trim() === String(person.notes || "").trim()}
          {...tip("Save this note. It is about the person, not one photo.")}
        >
          {notesState === "saving" ? "Saving…" : notesState === "saved" ? "Saved" : "Save note"}
        </button>
      </form>
      <div style={{ marginBottom: 16 }}>
        <FamousLookup
          personId={person.id}
          onName={(suggested) => {
            if (suggested === "") {
              setName("");
              return;
            }
            setName((cur) => (cur.trim() ? cur : suggested));
          }}
          onApplyExisting={(pid, result) => {
            if (String(pid) === String(person.id)) {
              setName(result.name);
              return;
            }
            api.mergePerson(pid, person.id).then(() => {
              emitCatalogChange();
              nav(`/people/${pid}`);
            });
          }}
          onConfirm={(hit) => {
            setName(hit.name);
            api.patchPerson(id, { name: hit.name }).then(load);
          }}
        />
      </div>
      <p className="hint" style={{ margin: "0 0 12px" }}>
        Labels are the year the picture was taken, not a guessed age. Remove a face if it is not
        this person.
      </p>
      <div className="timeline">
        {shots.map((f) => {
          const rot = (((Number(f.rotation) || 0) % 360) + 360) % 360;
          const w = Number(f.photo_width) || 0;
          const h = Number(f.photo_height) || 0;
          const picStyle = rot
            ? {
                "--rot": `${rot}deg`,
                ...(w && h ? { "--ow": w, "--oh": h } : {}),
              }
            : undefined;
          return (
          <div key={f.id} className="timeline-shot" id={personShotHash(f.photo_id)}>
            <div
              className={`timeline-shot-pic${rot ? ` rot-${rot}` : ""}`}
              style={picStyle}
              onContextMenu={(event) =>
                showPhotoMenu(event, {
                  id: f.photo_id,
                  filename: f.filename,
                  path: f.path,
                  tags: f.tags || [],
                  faces: [{ ...f, person_name: person.name }],
                  thumb_url: f.thumb_url,
                  width: f.photo_width,
                  height: f.photo_height,
                  rotation: rot,
                })
              }
            >
            <Link
              to={`/photos/${f.photo_id}?person=${person.id}`}
              state={{
                from: `/people/${person.id}#${personShotHash(f.photo_id)}`,
                rotation: rot,
                photoId: Number(f.photo_id),
              }}
              onClick={() => {
                rememberPhotoRotation(f.photo_id, rot);
                writePersonPos({ personId: person.id, photoId: Number(f.photo_id) });
              }}
            >
              <img
                src={f.thumb_url || `/api/photos/${f.photo_id}/thumb`}
                alt=""
                data-face-id={f.id}
                loading="lazy"
                decoding="async"
                width={f.photo_width || undefined}
                height={f.photo_height || undefined}
                style={
                  !rot && w && h
                    ? { aspectRatio: `${w} / ${h}` }
                    : undefined
                }
                onError={(e) => {
                  if (f.crop_url && e.currentTarget.src !== f.crop_url) {
                    e.currentTarget.src = f.crop_url;
                  }
                }}
              />
            </Link>
            {(f.tags || []).length ? <PhotoTagRow tags={f.tags} link /> : null}
            </div>
            <div className="hint">{faceWhen(f) || "no date"}</div>
            <button
              type="button"
              className="secondary"
              onClick={() => removeShot(f)}
              {...tip("This face is not this person. It goes back to unnamed. The matcher will not put the name back.")}
            >
              Remove
            </button>
          </div>
          );
        })}
      </div>
      {people.length ? (
        <details className="merge-fold" style={{ marginTop: 28 }}>
          <summary>
            Join with another identity
            <span className="hint"> — only if they are the same person at a different age</span>
          </summary>
          <p className="hint">
            Everyone listed here is a different identity. Do not click a face unless you are sure
            it is this same person, older or younger.
          </p>
          <PersonPicker
            people={people}
            hint="Click a face only to merge the same person at two ages."
            onPick={(p) =>
              api.mergePerson(id, p.id).then(() => {
                emitCatalogChange();
                load();
              })
            }
          />
        </details>
      ) : null}
    </div>
  );
}
