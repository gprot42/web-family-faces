import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";

export default function PeopleSearch() {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [nameHits, setNameHits] = useState([]);
  const [nameBusy, setNameBusy] = useState(false);
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState("");
  const [faceHits, setFaceHits] = useState(null);
  const [photoHits, setPhotoHits] = useState(null);
  const [facesFound, setFacesFound] = useState(0);
  const [faceBusy, setFaceBusy] = useState(false);
  const [err, setErr] = useState("");
  const nameRef = useRef(null);
  const fileRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const t = window.setTimeout(() => nameRef.current?.focus(), 40);
    function onKey(event) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    const needle = q.trim();
    if (!open || needle.length < 2) {
      setNameHits([]);
      setNameBusy(false);
      return undefined;
    }
    let cancelled = false;
    setNameBusy(true);
    const t = window.setTimeout(() => {
      api
        .search(needle, "name")
        .then((found) => {
          if (!cancelled) setNameHits(found.people || []);
        })
        .catch((ex) => {
          if (!cancelled) setErr(ex.message || "Could not search names.");
        })
        .finally(() => {
          if (!cancelled) setNameBusy(false);
        });
    }, 180);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [q, open]);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const nameList = useMemo(
    () => nameHits.filter((p) => !p.unknown_name).slice(0, 8),
    [nameHits],
  );

  function pickFile(next) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(next || null);
    setPreview(next ? URL.createObjectURL(next) : "");
    setFaceHits(null);
    setPhotoHits(null);
    setFacesFound(0);
    setErr("");
  }

  async function searchPhoto(nextFile) {
    const upload = nextFile || file;
    if (!upload || faceBusy) return;
    setFaceBusy(true);
    setErr("");
    try {
      api.warmupFaces().catch(() => {});
      const found = await api.searchFace(upload);
      setFacesFound(Number(found.faces_found) || 0);
      setFaceHits(found.people || []);
      setPhotoHits(found.photos || []);
    } catch (ex) {
      setFaceHits(null);
      setPhotoHits(null);
      setErr(ex.message || "Could not search that photo.");
    } finally {
      setFaceBusy(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="people-search-fab"
        aria-expanded={open}
        aria-controls="people-search-panel"
        onClick={() => setOpen((cur) => !cur)}
        {...tip("Find someone by name, or upload a photo to match a picture and faces already in the catalog.")}
      >
        Search
      </button>
      {open ? (
        <div className="people-search-panel" id="people-search-panel" role="dialog" aria-label="Search people">
          <div className="people-search-head">
            <p className="eyebrow">Find someone</p>
            <button type="button" className="ghost" onClick={() => setOpen(false)}>
              Close
            </button>
          </div>
          <label className="cluster-label" htmlFor="people-search-name">
            Search by name
          </label>
          <input
            id="people-search-name"
            ref={nameRef}
            className="grow"
            type="search"
            value={q}
            placeholder="Sam, Clara…"
            onChange={(e) => {
              setQ(e.target.value);
              setErr("");
            }}
          />
          {nameBusy ? <p className="hint">Looking up names…</p> : null}
          {q.trim().length >= 2 && !nameBusy && !nameList.length ? (
            <p className="hint">No names match “{q.trim()}”.</p>
          ) : null}
          {nameList.length ? (
            <div className="people-search-hits">
              {nameList.map((person) => (
                <Link key={person.id} className="people-search-hit" to={`/people/${person.id}`} onClick={() => setOpen(false)}>
                  {person.cover_url ? <img src={person.cover_url} alt="" /> : <span className="person-picker-gap" />}
                  <span>
                    <strong>{person.name}</strong>
                    {person.nickname ? <span className="hint"> · {person.nickname}</span> : null}
                    <span className="hint">
                      {" "}
                      · {person.face_count || 0} photo{(person.face_count || 0) === 1 ? "" : "s"}
                    </span>
                  </span>
                </Link>
              ))}
            </div>
          ) : null}
          <div className="people-search-or">or</div>
          <p className="cluster-label">Upload a photo</p>
          <p className="hint" style={{ marginTop: -4 }}>
            Match a snapshot to a picture already in the catalog, and to people already named. The file is not added to the album.
          </p>
          <div className="row" style={{ marginTop: 8 }}>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              hidden
              onChange={(e) => {
                const next = e.target.files?.[0];
                e.target.value = "";
                if (!next) return;
                pickFile(next);
                searchPhoto(next);
              }}
            />
            <button
              type="button"
              className="secondary"
              disabled={faceBusy}
              onClick={() => fileRef.current?.click()}
              {...tip("Choose a JPEG, PNG, or HEIC. Family Faces looks for a face and matches it to the catalog.")}
            >
              {faceBusy ? "Matching…" : preview ? "Choose another photo" : "Choose photo"}
            </button>
          </div>
          {preview ? (
            <img className="people-search-preview" src={preview} alt="Uploaded photo preview" />
          ) : null}
          {err ? <p className="error">{err}</p> : null}
          {faceHits && photoHits && !faceHits.length && !photoHits.length ? (
            <p className="hint">
              {facesFound
                ? "Found a face, but it does not match a catalog photo or a named person closely enough."
                : "No face found in that photo. Try a clearer, closer shot."}
            </p>
          ) : null}
          {photoHits?.length ? (
            <>
              <p className="cluster-label" style={{ marginTop: 12 }}>
                In your archive
              </p>
              <div className="people-search-hits">
                {photoHits.map((photo) => (
                  <Link
                    key={photo.id}
                    className="people-search-hit photo-hit"
                    to={`/photos/${photo.id}`}
                    onClick={() => setOpen(false)}
                  >
                    {photo.thumb_url ? <img src={photo.thumb_url} alt="" /> : <span className="person-picker-gap" />}
                    <span>
                      <strong>{photo.filename}</strong>
                      <span className="hint">
                        {photo.folder ? ` · ${photo.folder}` : ""}
                        {photo.exact
                          ? " · exact file"
                          : ` · ${Math.round((photo.similarity || 0) * 100)}% same face`}
                        {photo.person_name ? ` · ${photo.person_name}` : ""}
                      </span>
                    </span>
                  </Link>
                ))}
              </div>
            </>
          ) : null}
          {faceHits?.length ? (
            <>
              <p className="cluster-label" style={{ marginTop: 12 }}>
                Named people
              </p>
              <div className="people-search-hits">
                {faceHits.map((person) => (
                  <Link key={person.id} className="people-search-hit" to={`/people/${person.id}`} onClick={() => setOpen(false)}>
                    {person.cover_url ? <img src={person.cover_url} alt="" /> : <span className="person-picker-gap" />}
                    <span>
                      <strong>{person.name}</strong>
                      {person.nickname ? <span className="hint"> · {person.nickname}</span> : null}
                      <span className="hint">
                        {" "}
                        · {Math.round((person.similarity || 0) * 100)}% match
                        {person.face_count ? ` · ${person.face_count} photo${person.face_count === 1 ? "" : "s"}` : ""}
                      </span>
                    </span>
                  </Link>
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
