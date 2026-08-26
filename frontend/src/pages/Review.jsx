import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";
import { faceWhen } from "../ages.js";

const REVIEW_PAGE = 24;
const REVIEW_MORE = 500;
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
    const prev = readReviewPos() || {};
    const next = { ...prev, ...pos };
    if (!(Number(next.scrollTop) > 0) && Number(prev.scrollTop) > 0 && (next.faceId || next.photoId)) {
      next.scrollTop = prev.scrollTop;
    }
    sessionStorage.setItem(POS_KEY, JSON.stringify(next));
  } catch {
    /* ignore quota */
  }
}

function reviewCardEl(pos) {
  if (!pos) return null;
  if (pos.faceId) {
    const byFace = document.querySelector(`[data-review-face="${pos.faceId}"]`);
    if (byFace) return byFace;
  }
  if (pos.photoId) {
    const byPhoto = pos.personId
      ? document.querySelector(`[data-review-person="${pos.personId}"] [data-review-photo="${pos.photoId}"]`)
      : document.querySelector(`[data-review-photo="${pos.photoId}"]`);
    if (byPhoto) return byPhoto;
  }
  if (pos.personId) return document.querySelector(`[data-review-person="${pos.personId}"]`);
  return null;
}

export default function Review({ onChange }) {
  const [groups, setGroups] = useState([]);
  const [count, setCount] = useState(0);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [moreBusy, setMoreBusy] = useState(null);
  const moreBusyRef = useRef(new Set());
  const groupsRef = useRef([]);
  const restoredPos = useRef(false);
  groupsRef.current = groups;

  async function load() {
    setLoading(true);
    try {
      const data = await api.reviewAuto({ limit: REVIEW_PAGE });
      const n = data.face_count || 0;
      const groups = await ensurePosFaces(data.items || []);
      setGroups(groups);
      setCount(n);
      onChange?.({ faces_auto: n }, "set");
    } finally {
      setLoading(false);
    }
  }

  async function ensurePosFaces(groups) {
    const pos = readReviewPos();
    if (!pos?.personId) return groups;
    const pid = Number(pos.personId);
    const group = groups.find((g) => Number(g.person.id) === pid);
    if (!group) return groups;
    const has =
      group.faces.some((f) => Number(f.id) === Number(pos.faceId)) ||
      group.faces.some((f) => Number(f.photo_id) === Number(pos.photoId));
    if (has || !(group.face_count > group.faces.length)) return groups;
    const have = new Set(group.faces.map((f) => f.id));
    const afterId = group.faces.reduce((m, f) => (Number(f.id) > m ? Number(f.id) : m), 0);
    const remaining = Math.max(0, (group.face_count || 0) - group.faces.length);
    try {
      const extraData = await api.reviewAuto({
        person_id: pid,
        after_id: afterId || undefined,
        offset: afterId ? 0 : group.faces.length,
        limit: Math.min(REVIEW_MORE, Math.max(REVIEW_PAGE, remaining)),
      });
      const payload =
        (extraData.items || []).find((item) => Number(item.person.id) === pid) || extraData.items?.[0];
      const extra = (payload?.faces || []).filter((f) => !have.has(f.id));
      if (!extra.length) return groups;
      const total = payload?.face_count ?? group.face_count;
      return groups.map((g) =>
        Number(g.person.id) === pid ? { ...g, faces: g.faces.concat(extra), face_count: total } : g,
      );
    } catch {
      return groups;
    }
  }

  async function showMore(personId) {
    const group = groupsRef.current.find((g) => g.person.id === personId);
    if (!group || moreBusyRef.current.has(personId)) return;
    moreBusyRef.current.add(personId);
    setMoreBusy(personId);
    setErr("");
    const have = new Set(group.faces.map((f) => f.id));
    const afterId = group.faces.reduce((m, f) => (Number(f.id) > m ? Number(f.id) : m), 0);
    const remaining = Math.max(0, (group.face_count || 0) - group.faces.length);
    try {
      const data = await api.reviewAuto({
        person_id: personId,
        after_id: afterId || undefined,
        offset: afterId ? 0 : group.faces.length,
        limit: Math.min(REVIEW_MORE, Math.max(REVIEW_PAGE, remaining)),
      });
      const payload =
        (data.items || []).find((item) => item.person.id === personId) || data.items?.[0];
      const extra = (payload?.faces || []).filter((f) => !have.has(f.id));
      const total = payload?.face_count ?? group.face_count;
      setGroups((cur) =>
        cur.map((g) =>
          g.person.id === personId
            ? { ...g, faces: extra.length ? g.faces.concat(extra) : g.faces, face_count: total }
            : g,
        ),
      );
      const firstNew = extra[0]?.id;
      if (firstNew) {
        requestAnimationFrame(() => {
          document
            .querySelector(`[data-review-face="${firstNew}"]`)
            ?.scrollIntoView({ block: "start", behavior: "smooth" });
        });
      } else if (remaining > 0) {
        setErr("Could not load more faces.");
      }
    } catch (ex) {
      setErr(ex.message || "Could not load more faces.");
    } finally {
      moreBusyRef.current.delete(personId);
      setMoreBusy((cur) => (cur === personId ? null : cur));
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
    const prev = window.history.scrollRestoration;
    try {
      window.history.scrollRestoration = "manual";
    } catch {
      /* ignore */
    }
    function onScroll() {
      const y = window.scrollY;
      if (y > 0) writeReviewPos({ scrollTop: y });
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      const y = window.scrollY;
      if (y > 0) writeReviewPos({ scrollTop: y });
      window.removeEventListener("scroll", onScroll);
      try {
        window.history.scrollRestoration = prev || "auto";
      } catch {
        /* ignore */
      }
    };
  }, []);

  useEffect(() => {
    if (loading || !groups.length || restoredPos.current) return;
    const pos = readReviewPos();
    if (!pos) {
      restoredPos.current = true;
      return undefined;
    }
    let tries = 0;
    let timer = 0;
    const tryApply = () => {
      const card = reviewCardEl(pos);
      if (card) {
        card.scrollIntoView({ block: "center", inline: "nearest" });
        restoredPos.current = true;
        return;
      }
      if (tries >= 16) {
        if (Number(pos.scrollTop) > 0) window.scrollTo(0, Number(pos.scrollTop));
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
  }, [loading, groups]);

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
      onChange?.();
    } catch (ex) {
      setErr(ex.message);
      await load();
    }
  }

  async function reject(face) {
    setErr("");
    const ids = face.face_ids?.length ? face.face_ids : [face.id];
    const removed = dropFaces(ids, undefined);
    onChange?.({ faces_auto: -removed });
    try {
      await api.unassignFace(face.id);
      onChange?.();
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
            the face back to Clusters to name. Names you typed yourself are not listed here.
          </p>
        </div>
      </div>
      {err ? <p className="error">{err}</p> : null}
      {loading ? (
        <div className="card empty">Loading auto-named faces…</div>
      ) : !groups.length ? (
        <div className="card empty">No auto-named faces waiting. Name someone on Clusters to name, then come back after Find Known Faces.</div>
      ) : (
        <p className="hint" style={{ marginTop: -8, marginBottom: 18 }}>
          {count} face{count === 1 ? "" : "s"} to check.
        </p>
      )}
      {groups.map((g) => (
        <section key={g.person.id} className="folder-block" data-review-person={g.person.id}>
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
                onKeep={() => keep(f.face_ids?.length ? f.face_ids : [f.id], g.person.id)}
                onReject={() => reject(f)}
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
              {...tip("Load the rest of this person's auto-named faces.")}
            >
              {moreBusy === g.person.id
                ? "Loading…"
                : `Show ${g.face_count - g.faces.length} more`}
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
