import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { imageBlobForClipboard } from "../copyPhoto.js";
import { applyFullscreenLabels, readFullscreenLabels } from "../nametag.js";
import { emitPhotoChange, subscribePhotoMenu } from "../photoMenu.js";
import { clearRematchUndo, readRematchUndo, writeRematchUndo } from "../rematchUndo.js";
import { pushFaceUndo, pushUndo } from "../editUndo.js";
import { boxIou, displayFaces, overlayFaces, unnamedName } from "./LabeledPhoto.jsx";
import { normalizeTag, TAGS_MAX, tagHref } from "./PhotoTags.jsx";

const PAD = 8;

export default function PhotoMenu() {
  const nav = useNavigate();
  const [menu, setMenu] = useState(null);
  const [busy, setBusy] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const box = useRef(null);
  const tagRef = useRef(null);

  useEffect(
    () =>
      subscribePhotoMenu((next) => {
        setTagDraft("");
        setMenu(next);
      }),
    [],
  );

  useEffect(() => {
    if (!menu) return undefined;
    function insideMenu(event) {
      if (!box.current) return false;
      if (event?.target && box.current.contains(event.target)) return true;
      if (event?.clientX == null || event?.clientY == null) return false;
      const r = box.current.getBoundingClientRect();
      return event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
    }
    function hide(event) {
      if (event?.type === "keydown") {
        if (event.key !== "Escape") return;
        setMenu(null);
        return;
      }
      if (insideMenu(event)) return;
      setMenu(null);
    }
    window.addEventListener("pointerdown", hide, true);
    window.addEventListener("keydown", hide);
    window.addEventListener("scroll", hide, true);
    return () => {
      window.removeEventListener("pointerdown", hide, true);
      window.removeEventListener("keydown", hide);
      window.removeEventListener("scroll", hide, true);
    };
  }, [menu]);

  useLayoutEffect(() => {
    if (!menu || !box.current) return;
    const r = box.current.getBoundingClientRect();
    let left = menu.x;
    let top = menu.y;
    if (left + r.width > window.innerWidth - PAD) left = window.innerWidth - r.width - PAD;
    if (top + r.height > window.innerHeight - PAD) top = window.innerHeight - r.height - PAD;
    if (left < PAD) left = PAD;
    if (top < PAD) top = PAD;
    box.current.style.left = `${left}px`;
    box.current.style.top = `${top}px`;
  }, [menu]);

  if (!menu) return null;
  const pendingUndo = readRematchUndo(menu.photo.id);

  async function copyPhoto() {
    if (busy) return;
    if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
      window.alert("This browser cannot copy images. Try Chrome or Safari.");
      return;
    }
    setBusy("copy");
    try {
      const png = await imageBlobForClipboard(menu.photo);
      await navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
      setMenu(null);
    } catch (ex) {
      window.alert(ex.message || "Could not copy this photo.");
    } finally {
      setBusy(false);
    }
  }

  async function rotate(direction) {
    if (busy) return;
    setBusy(true);
    try {
      const next = await api.patchPhoto(menu.photo.id, { rotate: direction });
      emitPhotoChange(next);
      setMenu(null);
    } catch (ex) {
      window.alert(ex.message || "Could not rotate this photo.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (busy) return;
    const ok = window.confirm(
      `Remove “${menu.photo.filename || "this photo"}” from Family Faces? The original file stays where it is. Find Known Faces will not show it again.`,
    );
    if (!ok) return;
    setBusy(true);
    try {
      const next = await api.patchPhoto(menu.photo.id, { hidden: true });
      emitPhotoChange({ ...next, hidden: true });
      setMenu(null);
      if (window.location.pathname === `/photos/${menu.photo.id}`) {
        nav(-1);
      }
    } catch (ex) {
      window.alert(ex.message || "Could not remove this photo from the catalog.");
    } finally {
      setBusy(false);
    }
  }

  const people = namedPeople(menu.photo, menu.faceId);
  const unnamed = unnamedFaces(menu.photo, menu.faceId);
  const tags = (menu.photo.tags || []).filter(Boolean);

  async function saveTags(next) {
    const prev = menu.photo;
    const tagsNext = Array.isArray(next) ? next : [];
    emitPhotoChange({ ...prev, tags: tagsNext });
    setMenu((cur) => (cur ? { ...cur, photo: { ...cur.photo, tags: tagsNext } } : cur));
    try {
      const result = await api.patchPhoto(prev.id, { tags: tagsNext });
      const saved = result.tags || [];
      emitPhotoChange({ ...prev, tags: saved, faces: prev.faces });
      setMenu((cur) => (cur ? { ...cur, photo: { ...cur.photo, tags: saved } } : cur));
    } catch (ex) {
      emitPhotoChange(prev);
      setMenu((cur) => (cur ? { ...cur, photo: prev } : cur));
      window.alert(ex.message || "Could not save the tag.");
    }
  }

  function addTag(raw) {
    const next = normalizeTag(raw);
    if (!next) return;
    if (tags.some((item) => item.toLowerCase() === next.toLowerCase())) {
      setTagDraft("");
      return;
    }
    if (tags.length >= TAGS_MAX) return;
    setTagDraft("");
    saveTags([...tags, next]);
  }

  async function removePeople(faceIds) {
    if (busy || !faceIds.length) return;
    setBusy("people");
    const prev = menu.photo;
    const targets = new Set();
    for (const faceId of faceIds) {
      const hit = (prev.faces || []).find((f) => f.id === faceId);
      if (hit?.person_id) targets.add(hit.person_id);
    }
    const faces = (prev.faces || []).map((f) =>
      targets.has(f.person_id) ? { ...f, person_id: null, person_name: null, assigned_how: null } : f,
    );
    emitPhotoChange({ ...prev, faces });
    setMenu(null);
    try {
      for (const faceId of faceIds) {
        const hit = (prev.faces || []).find((f) => f.id === faceId);
        if (hit) pushFaceUndo(prev, hit, "removing the name");
        await api.unassignFace(faceId);
      }
    } catch (ex) {
      emitPhotoChange(prev);
      window.alert(ex.message || "Could not remove this name.");
    } finally {
      setBusy(false);
    }
  }

  async function removeUnnamed(faceIds) {
    const wanted = (faceIds || []).map((id) => Number(id)).filter((id) => Number.isFinite(id));
    if (!wanted.length) return;
    const prev = menu.photo;
    const hide = new Set([...wanted, ...unnamedToHide(prev.faces || [], wanted)]);
    if (!hide.size) return;
    const faces = (prev.faces || []).map((f) =>
      hide.has(Number(f.id)) ? { ...f, person_id: null, person_name: null, assigned_how: "junk" } : f,
    );
    emitPhotoChange({ ...prev, faces });
    setMenu(null);
    setBusy(false);
    try {
      for (const faceId of hide) {
        const hit = (prev.faces || []).find((f) => Number(f.id) === Number(faceId));
        if (hit) pushFaceUndo(prev, hit, "hiding this face");
        await api.junkFace(faceId);
      }
    } catch (ex) {
      emitPhotoChange(prev);
      window.alert(ex.message || "Could not hide this face.");
    }
  }

  async function removeAllUnnamed() {
    const prev = menu.photo;
    const hide = (prev.faces || []).filter((f) => !f.person_id && f.assigned_how !== "junk");
    if (!hide.length) return;
    const hideIds = new Set(hide.map((f) => Number(f.id)));
    const faces = (prev.faces || []).map((f) =>
      hideIds.has(Number(f.id)) ? { ...f, person_id: null, person_name: null, assigned_how: "junk" } : f,
    );
    emitPhotoChange({ ...prev, faces });
    setMenu(null);
    try {
      for (const hit of hide) {
        pushFaceUndo(prev, hit, "hiding this face");
      }
      await api.junkUnnamedPhoto(prev.id);
    } catch (ex) {
      emitPhotoChange(prev);
      window.alert(ex.message || "Could not hide unnamed faces.");
    }
  }

  return (
    <div
      ref={box}
      className="photo-menu"
      role="menu"
      aria-label="Photo"
      onPointerDown={(e) => e.stopPropagation()}
      onWheel={(e) => e.stopPropagation()}
      onScroll={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
    >
      <button type="button" role="menuitem" disabled={!!busy} onClick={copyPhoto}>
        {busy === "copy" ? "Copying…" : "Copy photo"}
      </button>
      <button type="button" role="menuitem" disabled={!!busy} onClick={() => rotate("left")}>
        Rotate left
      </button>
      <button type="button" role="menuitem" disabled={!!busy} onClick={() => rotate("right")}>
        Rotate right
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          applyFullscreenLabels(!readFullscreenLabels());
          setMenu(null);
        }}
      >
        {readFullscreenLabels() ? "Hide labels" : "Show labels"}
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const search = window.location.search || "";
          setMenu(null);
          nav(`/photos/${menu.photo.id}${search}`, { state: { markFace: true } });
        }}
      >
        Add a face
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const search = window.location.search || "";
          setMenu(null);
          nav(`/photos/${menu.photo.id}${search}`, { state: { comment: true } });
        }}
      >
        {menu.photo.comment ? "Edit comment" : "Add comment"}
      </button>
      <div className="photo-menu-sep" role="separator" />
      {tags.map((tag) => (
        <div key={tag} className="photo-menu-tag">
          <button
            type="button"
            role="menuitem"
            disabled={!!busy}
            onClick={() => {
              setMenu(null);
              nav(tagHref(tag));
            }}
          >
            {`View tag “${tag}”`}
          </button>
          <button
            type="button"
            className="photo-menu-tag-x"
            disabled={!!busy}
            aria-label={`Remove tag ${tag}`}
            onClick={() => saveTags(tags.filter((item) => item !== tag))}
          >
            ×
          </button>
        </div>
      ))}
      {tags.length < TAGS_MAX ? (
        <input
          ref={tagRef}
          className="photo-menu-input"
          type="text"
          maxLength={40}
          value={tagDraft}
          placeholder="Add a tag"
          aria-label="Add a tag"
          disabled={!!busy}
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              e.stopPropagation();
              addTag(tagDraft);
            }
          }}
        />
      ) : null}
      <div className="photo-menu-sep" role="separator" />
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const search = window.location.search || "";
          setMenu(null);
          nav(`/photos/${menu.photo.id}${search}`, { state: { sharpen: true } });
        }}
      >
        Sharpen with Grok
      </button>
      <button
        type="button"
        role="menuitem"
        onClick={() => {
          const search = window.location.search || "";
          setMenu(null);
          nav(`/photos/${menu.photo.id}${search}`, { state: { imagine: true } });
        }}
      >
        Change with Grok…
      </button>
      <button
        type="button"
        role="menuitem"
        disabled={!!busy}
        onClick={() => {
          if (busy) return;
          const photoId = menu.photo.id;
          setMenu(null);
          (async () => {
            try {
              const result = await api.waitMatchPhoto(photoId);
              const faceIds = [
                ...new Set((result.assigned || []).map((row) => Number(row.face_id)).filter((fid) => fid > 0)),
              ];
              const names = [...new Set((result.assigned || []).map((row) => row.name).filter(Boolean))];
              if (faceIds.length) {
                writeRematchUndo(photoId, faceIds, names);
                pushUndo({ type: "rematch", photoId, faceIds, label: "re-identify" });
              } else clearRematchUndo(photoId);
              const next = await api.photo(photoId, { lite: 1 });
              emitPhotoChange(next);
            } catch (ex) {
              if (ex.name === "AbortError") return;
              window.alert(ex.message || "Could not re-identify faces.");
            }
          })();
        }}
      >
        Re-identify faces
      </button>
      {pendingUndo?.faceIds?.length ? (
        <button
          type="button"
          role="menuitem"
          disabled={!!busy}
          onClick={async () => {
            if (busy) return;
            const pending = readRematchUndo(menu.photo.id);
            if (!pending?.faceIds?.length) return;
            setBusy("undo");
            try {
              await api.undoMatchPhoto(menu.photo.id, pending.faceIds);
              clearRematchUndo(menu.photo.id);
              const next = await api.photo(menu.photo.id, { lite: 1 });
              emitPhotoChange(next);
              setMenu(null);
            } catch (ex) {
              window.alert(ex.message || "Could not undo re-identify.");
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy === "undo"
            ? "Undoing…"
            : pendingUndo.faceIds.length === 1
              ? "Undo name"
              : `Undo ${pendingUndo.faceIds.length} names`}
        </button>
      ) : null}
      {people.length ? <div className="photo-menu-sep" role="separator" /> : null}
      {people.map((p) => (
        <button
          key={p.person_id}
          type="button"
          role="menuitem"
          disabled={!!busy}
          onClick={() => removePeople([p.face_id])}
        >
          {busy === "people" ? "Removing…" : `Remove ${p.name}`}
        </button>
      ))}
      {people.length > 1 ? (
        <button type="button" role="menuitem" disabled={!!busy} onClick={() => removePeople(people.map((p) => p.face_id))}>
          Remove all names
        </button>
      ) : null}
      {unnamed.length ? <div className="photo-menu-sep" role="separator" /> : null}
      {unnamed.map((u) => (
        <button
          key={u.face_id}
          type="button"
          role="menuitem"
          onClick={() => removeUnnamed([u.face_id])}
        >
          {busy === "unnamed" ? "Removing…" : `Remove ${u.label}`}
        </button>
      ))}
      {unnamed.length > 1 ? (
        <button type="button" role="menuitem" onClick={removeAllUnnamed}>
          Remove all unnamed
        </button>
      ) : null}
      <div className="photo-menu-sep" role="separator" />
      <button type="button" role="menuitem" className="danger" disabled={!!busy} onClick={remove}>
        Delete photo
      </button>
    </div>
  );
}

function namedPeople(photo, preferFaceId) {
  const seen = new Map();
  for (const face of photo?.faces || []) {
    if (!face?.person_id || face.assigned_how === "junk") continue;
    if (seen.has(face.person_id)) continue;
    const raw = String(face.person_name || "").trim();
    const name = !raw || raw.startsWith("Unknown name of person") ? "unknown name" : raw;
    seen.set(face.person_id, { person_id: face.person_id, name, face_id: face.id });
  }
  const items = [...seen.values()];
  if (!preferFaceId) return items;
  const preferred = (photo?.faces || []).find((f) => f.id === preferFaceId)?.person_id;
  if (!preferred) return items;
  return items.sort((a, b) => Number(b.person_id === preferred) - Number(a.person_id === preferred));
}

function unnamedFaces(photo, preferFaceId) {
  const shown = overlayFaces(photo?.faces || []);
  const items = shown
    .filter((f) => !f.person_id)
    .map((f) => ({ face_id: f.id, label: unnamedName(f, shown) }));
  if (preferFaceId) {
    const hit = (photo?.faces || []).find((f) => Number(f.id) === Number(preferFaceId));
    if (hit && !hit.person_id && !items.some((item) => Number(item.face_id) === Number(hit.id))) {
      items.unshift({ face_id: hit.id, label: unnamedName(hit, shown) || unnamedName(hit, displayFaces(photo.faces)) || "unnamed1" });
    }
    return items.sort((a, b) => Number(b.face_id === preferFaceId) - Number(a.face_id === preferFaceId));
  }
  return items;
}

function unnamedToHide(faces, faceIds) {
  const wanted = new Set((faceIds || []).map((id) => Number(id)));
  const ids = [];
  for (const face of faces || []) {
    const id = Number(face.id);
    if (!wanted.has(id)) continue;
    ids.push(id);
    for (const other of faces) {
      if (other.person_id || other.assigned_how === "junk") continue;
      const oid = Number(other.id);
      if (ids.includes(oid)) continue;
      if (face.photo_id != null && other.photo_id != null && String(face.photo_id) !== String(other.photo_id)) {
        continue;
      }
      if (boxIou(face, other) >= 0.72) ids.push(oid);
    }
  }
  return ids;
}


