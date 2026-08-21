import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";
import { faceWhen } from "../ages.js";

const REVIEW_PAGE = 24;
let sharedIO = null;
const nearSetters = new WeakMap();

function getSharedIO() {
  if (sharedIO) return sharedIO;
  sharedIO = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const setNear = nearSetters.get(entry.target);
        if (setNear) {
          setNear(true);
          nearSetters.delete(entry.target);
        }
        sharedIO.unobserve(entry.target);
      }
    },
    { rootMargin: "320px 0px" },
  );
  return sharedIO;
}

function useNearViewport(startNear) {
  const ref = useRef(null);
  const [near, setNear] = useState(Boolean(startNear));
  useEffect(() => {
    if (startNear || near) return undefined;
    const node = ref.current;
    if (!node) return undefined;
    const io = getSharedIO();
    nearSetters.set(node, setNear);
    io.observe(node);
    return () => {
      nearSetters.delete(node);
      io.unobserve(node);
    };
  }, [startNear, near]);
  return [ref, near];
}

const POS_KEY = "photosort-review-pos";

function readReviewPos() {
  try {
    return JSON.parse(sessionStorage.getItem(POS_KEY) || "null");
  } catch {
    return null;
  }
}

function writeReviewPos(pos) {
  try {
    sessionStorage.setItem(POS_KEY, JSON.stringify(pos));
  } catch {
    /* ignore quota */
  }
}

export default function Review({ onChange }) {
  const [groups, setGroups] = useState([]);
  const [count, setCount] = useState(0);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [moreBusy, setMoreBusy] = useState(null);
  const restoredPos = useRef(false);

  async function load() {
    setLoading(true);
    try {
      const data = await api.reviewAuto({ limit: REVIEW_PAGE });
      const n = data.face_count || 0;
      setGroups(data.items || []);
      setCount(n);
      onChange?.({ faces_auto: n }, "set");
    } finally {
      setLoading(false);
    }
  }

  async function showMore(personId) {
    const group = groups.find((g) => g.person.id === personId);
    if (!group || moreBusy) return;
    setMoreBusy(personId);
    setErr("");
    try {
      const data = await api.reviewAuto({
        person_id: personId,
        offset: group.faces.length,
        limit: REVIEW_PAGE,
      });
      const extra = data.items?.[0]?.faces || [];
      const total = data.items?.[0]?.face_count ?? group.face_count;
      setGroups((cur) =>
        cur.map((g) =>
          g.person.id === personId
            ? { ...g, faces: g.faces.concat(extra), face_count: total }
            : g,
        ),
      );
    } catch (ex) {
      setErr(ex.message || "Could not load more faces.");
    } finally {
      setMoreBusy(null);
    }
  }

  useEffect(() => {
    load().catch((ex) => {
      setErr(ex.message);
      setLoading(false);
    });
  }, []);

  function rememberPos(extra = {}) {
    writeReviewPos({
      scrollTop: window.scrollY,
      ...extra,
    });
  }

  useEffect(() => {
    function onScroll() {
      writeReviewPos({
        ...(readReviewPos() || {}),
        scrollTop: window.scrollY,
      });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      onScroll();
      window.removeEventListener("scroll", onScroll);
    };
  }, []);

  useEffect(() => {
    if (!groups.length || restoredPos.current) return;
    const pos = readReviewPos();
    if (!pos) return;
    restoredPos.current = true;
    const apply = () => {
      const card = pos.faceId
        ? document.querySelector(`[data-review-face="${pos.faceId}"]`)
        : pos.photoId
          ? document.querySelector(`[data-review-photo="${pos.photoId}"]`)
          : null;
      if (card) card.scrollIntoView({ block: "center" });
      else window.scrollTo(0, Number(pos.scrollTop) || 0);
    };
    requestAnimationFrame(() => requestAnimationFrame(apply));
  }, [groups]);

  function dropCount(faceIds, personId) {
    const drop = faceIds ? new Set(faceIds) : null;
    let n = 0;
    for (const g of groups) {
      if (personId && g.person.id !== personId) continue;
      if (drop) n += g.faces.filter((f) => drop.has(f.id)).length;
      else n += Number(g.face_count || g.faces.length);
    }
    return n;
  }

  function dropFaces(faceIds, personId) {
    const drop = faceIds ? new Set(faceIds) : null;
    const removed = dropCount(faceIds, personId);
    setGroups((cur) =>
      cur
        .map((g) => {
          if (personId && g.person.id !== personId) return g;
          if (!drop) return { ...g, faces: [], face_count: 0 };
          const faces = g.faces.filter((f) => !drop.has(f.id));
          const lost = g.faces.length - faces.length;
          return { ...g, faces, face_count: Math.max(0, (g.face_count || g.faces.length) - lost) };
        })
        .filter((g) => (g.face_count || g.faces.length) > 0),
    );
    setCount((n) => Math.max(0, n - removed));
    return removed;
  }

  async function keep(faceIds, personId) {
    setErr("");
    const removed = dropFaces(faceIds, personId);
    onChange?.({ faces_auto: -removed });
    try {
      await api.confirmAuto({ face_ids: faceIds, person_id: personId });
    } catch (ex) {
      setErr(ex.message);
      await load();
    }
  }

  async function reject(faceId) {
    setErr("");
    const removed = dropFaces([faceId]);
    onChange?.({ faces_auto: -removed });
    try {
      await api.unassignFace(faceId);
    } catch (ex) {
      setErr(ex.message);
      await load();
    }
  }

  const eagerIds = (() => {
    const ids = new Set();
    for (const g of groups) {
      for (const f of g.faces) {
        ids.add(f.id);
        if (ids.size >= 12) return ids;
      }
    }
    return ids;
  })();

  return (
    <div className="review-page">
      <div className="page-head">
        <div>
          <p className="eyebrow">Inbox</p>
          <h1>Check auto names</h1>
          <p className="lede">
            The matcher attached these names. Keep the ones that are right. Not this person sends
            the face back to To name. Names you typed yourself are not listed here.
          </p>
        </div>
      </div>
      {err ? <p className="error">{err}</p> : null}
      {loading ? (
        <div className="card empty">Loading auto-named faces…</div>
      ) : !groups.length ? (
        <div className="card empty">No auto-named faces waiting. Name someone on To name, then come back after Find Known Faces.</div>
      ) : (
        <p className="hint" style={{ marginTop: -8, marginBottom: 18 }}>
          {count} face{count === 1 ? "" : "s"} to check.
        </p>
      )}
      {groups.map((g) => (
        <section key={g.person.id} className="folder-block">
          <h2 className="folder-head">
            <span>
              <Link to={`/people/${g.person.id}`}>{g.person.unknown_name ? "Name unknown" : g.person.name}</Link>
              <span className="hint">
                {" "}
                · {g.face_count || g.faces.length} auto-named face
                {(g.face_count || g.faces.length) === 1 ? "" : "s"}
              </span>
            </span>
            <button
              type="button"
              className="ghost"
              onClick={() => keep(undefined, g.person.id)}
              {...tip(`Keep every auto-named face as ${g.person.name}.`)}
            >
              Keep all
            </button>
          </h2>
          <div className="review-grid">
            {g.faces.map((f) => (
              <ReviewCard
                key={f.id}
                face={f}
                person={g.person}
                eager={eagerIds.has(f.id)}
                onOpen={() => rememberPos({ faceId: f.id, photoId: f.photo_id, personId: g.person.id })}
                onKeep={() => keep([f.id])}
                onReject={() => reject(f.id)}
              />
            ))}
          </div>
          {(g.face_count || 0) > g.faces.length ? (
            <button
              type="button"
              className="secondary"
              style={{ marginTop: 12 }}
              disabled={moreBusy === g.person.id}
              onClick={() => showMore(g.person.id)}
              {...tip("Load the next set of auto-named faces for this person.")}
            >
              {moreBusy === g.person.id
                ? "Loading…"
                : `Show more · ${g.face_count - g.faces.length} left`}
            </button>
          ) : null}
        </section>
      ))}
    </div>
  );
}

function ReviewCard({ face, person, eager, onOpen, onKeep, onReject }) {
  const [ref, near] = useNearViewport(eager);
  return (
    <div
      ref={ref}
      className="card review-card"
      data-review-face={face.id}
      data-review-photo={face.photo_id}
    >
      <Link
        to={`/photos/${face.photo_id}?person=${person.id}`}
        state={{ fullscreen: true, from: "/review" }}
        onClick={onOpen}
      >
        {near ? (
          <img src={face.crop_url} alt="" decoding="async" />
        ) : (
          <span className="review-ph" />
        )}
      </Link>
      <div className="hint">
        {face.filename || "Photo"}
        {faceWhen(face) ? ` · ${faceWhen(face)}` : ""}
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <button type="button" onClick={onKeep} {...tip(`Yes, this is ${person.name}.`)}>
          Keep
        </button>
        <button
          type="button"
          className="secondary"
          onClick={onReject}
          {...tip("This is someone else. The face goes back to unnamed.")}
        >
          Not this person
        </button>
      </div>
    </div>
  );
}
