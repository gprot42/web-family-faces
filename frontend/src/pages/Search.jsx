import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { PHOTO_CHANGE_EVENT } from "../photoMenu.js";
import { tip } from "../tip.js";

function albumOf(path) {
  const parts = String(path || "").split("/").filter(Boolean);
  return parts.length >= 2 ? parts[parts.length - 2] : "";
}
import LabeledPhoto from "../components/LabeledPhoto.jsx";

export default function Search() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const q = (params.get("q") || "").trim();
  const byPhoto = params.get("by") === "photo";
  const [typed, setTyped] = useState(q);
  const [data, setData] = useState({ people: [], photos: [] });
  const [err, setErr] = useState("");

  function href(nextBy, query = typed) {
    const next = new URLSearchParams();
    next.set("by", nextBy);
    if (query.trim()) next.set("q", query.trim());
    return `/search?${next}`;
  }

  useEffect(() => {
    setTyped(q);
    if (!q) {
      setData({ people: [], photos: [] });
      return undefined;
    }
    let cancelled = false;
    api
      .search(q, byPhoto ? "photo" : "name")
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch((ex) => {
        if (!cancelled) setErr(ex.message);
      });
    return () => {
      cancelled = true;
    };
  }, [q, byPhoto]);

  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next?.id) return;
      setData((cur) => ({
        ...cur,
        photos: next.hidden
          ? (cur.photos || []).filter((p) => p.id !== next.id)
          : (cur.photos || []).map((p) => (p.id === next.id ? { ...p, ...next } : p)),
      }));
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, []);

  return (
    <div>
      <div className="page-head">
        <div>
          <p className="eyebrow">Find</p>
          <h1>{byPhoto ? "Find by photo" : "Find by name"}</h1>
          <p className="lede">
            {byPhoto
              ? "Type a filename or folder to find a picture in the catalog. The grid shows saved previews. Open a photo to read the original from the NAS if the share is mounted."
              : "Type a name or nickname to open that person, or the photos they appear in. Names live in the local database."}
          </p>
        </div>
      </div>
      <div className="person-chips" role="tablist" aria-label="Find">
        <Link
          className={`person-chip ${!byPhoto ? "active" : ""}`}
          to={href("name")}
          {...tip("Find a person already stored in the database.")}
        >
          Find by name
        </Link>
        <Link
          className={`person-chip ${byPhoto ? "active" : ""}`}
          to={href("photo")}
          {...tip("Find a photo by filename or folder.")}
        >
          Find by photo
        </Link>
      </div>
      <form
        className="row"
        style={{ marginBottom: 22 }}
        onSubmit={(e) => {
          e.preventDefault();
          nav(href(byPhoto ? "photo" : "name"));
        }}
      >
        <input
          className="grow"
          type="search"
          value={typed}
          placeholder={byPhoto ? "DSCN0994, Liverpool…" : "Sam, Clara…"}
          onChange={(e) => setTyped(e.target.value)}
          autoFocus
        />
        <button type="submit" className="secondary" {...tip(byPhoto ? "Find photos that match this filename or folder." : "Find people and photos whose names match.")}>
          {byPhoto ? "Find photo" : "Find name"}
        </button>
      </form>
      {err ? <p className="error">{err}</p> : null}
      {!q ? (
        <div className="card empty">
          {byPhoto ? "Type a filename or folder. Results stay on this Mac." : "Type a name. Results stay on this Mac."}
        </div>
      ) : null}
      {q && !data.people.length && !data.photos.length ? (
        <div className="card empty">
          {byPhoto ? `No photos match “${q}”.` : `No names match “${q}”.`}
        </div>
      ) : null}
      {data.people.length ? (
        <section className="folder-block">
          <h2>
            People
            <span className="hint"> · {data.people.length} in the database</span>
          </h2>
          <div className="people-grid">
            {data.people.map((p) => (
              <Link key={p.id} className="card person-card" to={`/people/${p.id}`}>
                <div className="person-head">
                  {p.cover_url ? <img src={p.cover_url} alt="" /> : <div className="person-head-empty" />}
                </div>
                <h3>{p.unknown_name ? "Name unknown" : p.name}</h3>
                {p.nickname ? <div className="person-nick">Also {p.nickname}</div> : null}
                <div className="hint">
                  {p.face_count || 0} photo{(p.face_count || 0) === 1 ? "" : "s"}
                </div>
              </Link>
            ))}
          </div>
        </section>
      ) : null}
      {data.photos.length ? (
        <section className="folder-block">
          <h2>
            Photos
            <span className="hint"> · {data.photos.length}</span>
          </h2>
          <div className="label-grid">
            {data.photos.map((p) => (
              <div key={p.id} className="label-card">
                <LabeledPhoto
                  photo={p}
                  src={p.thumb_url}
                  to={`/photos/${p.id}${p.match_person_id ? `?person=${p.match_person_id}` : ""}`}
                  toState={{ fullscreen: true, from: `/search?${params.toString()}` }}
                />
                <div className="meta">
                  {p.filename}
                  {byPhoto && albumOf(p.path) ? ` · ${albumOf(p.path)}` : ""}
                  {p.match_person_name ? ` · ${p.match_person_name}` : ""}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
