import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { tip } from "../tip.js";
import { faceWhen } from "../ages.js";
import BackButton from "../components/BackButton.jsx";
import ViewSwitch from "../components/ViewSwitch.jsx";
import LabeledPhoto, { displayFaces, faceMark, faceTone, overlayFaces, unnamedName } from "../components/LabeledPhoto.jsx";
import { applyFullscreenLabels, FULLSCREEN_LABELS_EVENT, readFullscreenLabels } from "../nametag.js";
import NamesToggle from "../components/NamesToggle.jsx";
import LabelLayoutToggle from "../components/LabelLayoutToggle.jsx";
import { PLAY_EVENT, enterBrowserFullscreen, exitBrowserFullscreen, playHref, playIndexOf, prefetchPlay, readPlay, readPlayIntervalMs, stopPlay, updatePlay } from "../play.js";
import PersonPicker from "../components/PersonPicker.jsx";
import FamousLookup from "../components/FamousLookup.jsx";
import ImaginePrompt from "../components/ImaginePrompt.jsx";
import NameSuggest from "../components/NameSuggest.jsx";
import { completeUniqueFirstName, matchPeople, uniqueFirstName } from "../nameSuggest.js";
import { loadCachedPeople, saveCachedPeople } from "../peopleCache.js";
import { emitCatalogChange, emitPhotoChange, PHOTO_CHANGE_EVENT, showPhotoMenu } from "../photoMenu.js";
import { clearRematchUndo, readRematchUndo, writeRematchUndo } from "../rematchUndo.js";
import { peekUndo, popUndo, pushFaceUndo, pushUndo, undoCount } from "../editUndo.js";
import { folderHashFrom, personIdFrom, personShotHash, readAlbumPos, writeAlbumPos, writePersonPos } from "../albumPos.js";

const ZOOM_MIN = 0.25;
const ZOOM_MAX = 4;
const ZOOM_STEP = 0.25;
const ZOOM_FIT = 0.75;
const ZOOM_PRESETS = [0.75, 1, 2, 4];
const FACE_CATEGORIES = [
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

const TOOLS_POS_KEY = "photosort-full-tools-pos-v2";
const TOOLS_PAD = 8;
const BACK_RESERVE_W = 128;
const BACK_RESERVE_H = 72;

function clamp(n, lo, hi) {
  return Math.min(hi, Math.max(lo, n));
}

function readToolsPos() {
  try {
    const raw = JSON.parse(localStorage.getItem(TOOLS_POS_KEY) || "null");
    if (!raw || typeof raw.x !== "number" || typeof raw.y !== "number") return null;
    if (!Number.isFinite(raw.x) || !Number.isFinite(raw.y)) return null;
    return { x: raw.x, y: raw.y };
  } catch {
    return null;
  }
}

function writeToolsPos(pos) {
  try {
    if (!pos) localStorage.removeItem(TOOLS_POS_KEY);
    else localStorage.setItem(TOOLS_POS_KEY, JSON.stringify({ x: Math.round(pos.x), y: Math.round(pos.y) }));
  } catch {
    /* private mode */
  }
}

function clampToolsPos(x, y, el) {
  const w = el?.offsetWidth || 320;
  const h = el?.offsetHeight || 48;
  const box = el?.offsetParent;
  const vw = box?.clientWidth || window.innerWidth;
  const vh = box?.clientHeight || window.innerHeight;
  const maxX = Math.max(TOOLS_PAD, vw - w - TOOLS_PAD);
  const maxY = Math.max(TOOLS_PAD, vh - h - TOOLS_PAD);
  let nx = clamp(Math.round(x), TOOLS_PAD, maxX);
  let ny = clamp(Math.round(y), TOOLS_PAD, maxY);
  if (nx < BACK_RESERVE_W && ny < BACK_RESERVE_H) {
    const right = BACK_RESERVE_W <= maxX ? BACK_RESERVE_W - nx : Infinity;
    const down = BACK_RESERVE_H <= maxY ? BACK_RESERVE_H - ny : Infinity;
    if (right <= down) nx = BACK_RESERVE_W;
    else ny = BACK_RESERVE_H;
  }
  return { x: nx, y: ny };
}

function isFitZoom(value) {
  return Math.abs(value - ZOOM_FIT) < 0.02;
}

function wheelZoomFactor(event) {
  let dy = event.deltaY;
  if (event.deltaMode === 1) dy *= 16;
  if (event.deltaMode === 2) dy *= 800;
  // Chromium reports a trackpad pinch as ctrl + wheel.
  const k = event.ctrlKey ? 0.01 : 0.0024;
  return Math.exp(-dy * k);
}

function photoSequence(photo) {
  const index = Number(photo?.photo_index);
  const count = Number(photo?.photo_count);
  if (!Number.isFinite(index) || !Number.isFinite(count) || index < 1 || count < 1) return null;
  return { index, count, label: `photo ${index} of ${count}` };
}

function mergePeopleKeepCovers(items, cur) {
  if (!cur?.length) return items;
  const prevById = new Map(cur.map((p) => [String(p.id), p]));
  return items.map((p) => {
    const prev = prevById.get(String(p.id));
    if (!prev?.cover_url || p.cover_url) return p;
    return { ...p, cover_url: prev.cover_url, cover_face_id: p.cover_face_id || prev.cover_face_id };
  });
}

function eventOrigin(event, el) {
  const host = (el || event.currentTarget)?.getBoundingClientRect?.();
  if (!host) return { x: 0, y: 0 };
  const cx = event.clientX ?? host.left + host.width / 2;
  const cy = event.clientY ?? host.top + host.height / 2;
  return { x: cx - host.left - host.width / 2, y: cy - host.top - host.height / 2 };
}

function photoImgUrl(photoId, kind) {
  return photoId ? `/api/photos/${photoId}/${kind}` : "";
}

export default function PhotoDetail() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const personId = params.get("person") || "";
  const tagFilter = (params.get("tag") || "").trim();
  const loc = useLocation();
  const nav = useNavigate();
  const [photo, setPhoto] = useState(null);
  const [active, setActive] = useState(null);
  const [name, setName] = useState("");
  const [drafts, setDrafts] = useState({});
  const [savingId, setSavingId] = useState(null);
  const [savedId, setSavedId] = useState(null);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [changingId, setChangingId] = useState(null);
  const [categories, setCategories] = useState({});
  const [people, setPeople] = useState(() => loadCachedPeople(""));
  const [photoSrc, setPhotoSrc] = useState(() => (id ? photoImgUrl(id, "thumb") : ""));
  const zoomRef = useRef(ZOOM_FIT);
  const [namePick, setNamePick] = useState(-1);
  const [commentDraft, setCommentDraft] = useState("");
  const [commentSaved, setCommentSaved] = useState(false);
  const [commentSaving, setCommentSaving] = useState(false);
  const [commentOpen, setCommentOpen] = useState(false);
  const [faceNoteOpen, setFaceNoteOpen] = useState(null);
  const [faceNotes, setFaceNotes] = useState({});
  const [faceNoteSaving, setFaceNoteSaving] = useState(null);
  const [sharpening, setSharpening] = useState(false);
  const [sharpenedUrl, setSharpenedUrl] = useState("");
  const [showSharpened, setShowSharpened] = useState(false);
  const sharpenWanted = useRef(false);
  const sharpeningNow = useRef(false);
  const [imagineOpen, setImagineOpen] = useState(false);
  const [imaginePrompt, setImaginePrompt] = useState("");
  const [imagining, setImagining] = useState(false);
  const [imaginedUrl, setImaginedUrl] = useState("");
  const [showImagined, setShowImagined] = useState(false);
  const imagineWanted = useRef(false);
  const imaginingNow = useRef(false);
  const [rematching, setRematching] = useState(false);
  const rematchAbort = useRef(null);
  const pageLive = useRef(true);
  const watchMatchRef = useRef(null);
  const [undoRematch, setUndoRematch] = useState(() => readRematchUndo(id));
  const [undoing, setUndoing] = useState(false);
  const [pickingFace, setPickingFace] = useState(false);
  const [pickingBusy, setPickingBusy] = useState(false);
  const pickingNow = useRef(false);
  const pickingBusyNow = useRef(false);
  const pickingGen = useRef(0);
  const cancelMarkFaceRef = useRef(() => {});
  const undoLastRef = useRef(() => Promise.resolve(false));
  const undoBusy = useRef(false);
  const undoWanted = useRef(0);
  const photoNow = useRef(null);
  const commentRef = useRef(null);
  const suggestionsFor = useRef(new Set());
  const [full, setFull] = useState(true);
  const [fullLabels, setFullLabels] = useState(() => readFullscreenLabels());
  const [play, setPlay] = useState(() => readPlay());
  const [zoom, setZoom] = useState(ZOOM_FIT);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [toolsPos, setToolsPos] = useState(() => readToolsPos());
  const [toolsDragging, setToolsDragging] = useState(false);
  const stageRef = useRef(null);
  const fullRef = useRef(null);
  const zoomBarRef = useRef(null);
  const toolsRef = useRef(null);
  const toolsPosNow = useRef(toolsPos);
  const toolsSaved = useRef(toolsPos);
  const toolsDrag = useRef(null);
  const fullNow = useRef(full);
  const idNow = useRef(id);
  const zoomNow = useRef(ZOOM_FIT);
  const panNow = useRef({ x: 0, y: 0 });
  const drag = useRef(null);
  const didDrag = useRef(false);
  const skipFullClick = useRef(false);
  const advancing = useRef(false);
  const stayNamed = useRef(false);
  fullNow.current = full;
  idNow.current = id;
  photoNow.current = photo;
  zoomNow.current = zoom;
  panNow.current = pan;
  toolsPosNow.current = toolsPos;
  pickingNow.current = pickingFace;
  pickingBusyNow.current = pickingBusy;

  function hrefFor(photoId) {
    if (!photoId) return "/photos?by=person";
    const session = readPlay();
    if (session) return playHref(photoId, session);
    if (personId) return `/photos/${photoId}?person=${personId}`;
    if (tagFilter) return `/photos/${photoId}?tag=${encodeURIComponent(tagFilter)}`;
    return `/photos/${photoId}`;
  }

  function safeFrom(value) {
    if (typeof value !== "string") return "";
    if (!value.startsWith("/") || value.startsWith("//")) return "";
    return value;
  }

  function personReturnTo(photoId = id) {
    const from = safeFrom(loc.state?.from);
    const pid = personIdFrom(from);
    if (pid && photoId) return `/people/${pid}#${personShotHash(photoId)}`;
    return from;
  }

  function backTo() {
    const from = personReturnTo() || safeFrom(loc.state?.from);
    if (from) return from;
    if (personId) return `/photos?by=person&person=${personId}`;
    if (tagFilter) return `/photos?by=tag&tag=${encodeURIComponent(tagFilter)}`;
    return "/photos";
  }

  function photoNavState(extra = {}, photoId = id) {
    const from = personReturnTo(photoId) || safeFrom(loc.state?.from);
    return {
      ...extra,
      ...(from ? { from } : {}),
    };
  }

  function rememberPersonPos(photoId = id) {
    const from = safeFrom(loc.state?.from);
    const pid = personIdFrom(from);
    if (!pid || !photoId) return;
    writePersonPos({ personId: pid, photoId: Number(photoId) });
  }

  function goPhoto(photoId) {
    if (!photoId) return;
    const session = readPlay();
    if (session?.ids?.length) {
      const idx = session.ids.indexOf(Number(photoId));
      if (idx >= 0) updatePlay({ index: idx });
    }
    advancing.current = Boolean(session);
    stayNamed.current = !fullNow.current && !session;
    rememberPersonPos(photoId);
    nav(
      hrefFor(photoId),
      { state: photoNavState(fullNow.current || session ? { fullscreen: true } : {}, photoId) },
    );
  }

  function playIndex() {
    return playIndexOf(play, id);
  }

  function goPlay(dir) {
    if (play?.ids?.length) {
      const i = playIndex();
      if (i >= 0) {
        const j = i + dir;
        if (j >= 0 && j < play.ids.length) {
          goPhoto(play.ids[j]);
          return;
        }
      }
    }
    goPhoto(dir > 0 ? photo?.next_id : photo?.prev_id);
  }

  function endPlay() {
    stopPlay();
    setPlay(null);
    setFull(false);
    exitBrowserFullscreen();
  }

  function togglePlay() {
    const session = readPlay();
    if (!session) return;
    setPlay(updatePlay({ playing: !session.playing }));
  }

  function inboxFrom(from) {
    return from === "/to-name" || from.startsWith("/to-name#") || from === "/review" || from.startsWith("/review#");
  }

  function goBack(e) {
    e?.preventDefault();
    if (fullNow.current) {
      stopPlay();
      setPlay(null);
      setFull(false);
      exitBrowserFullscreen();
    }
    const from = safeFrom(loc.state?.from);
    if (inboxFrom(from)) {
      nav(from, { replace: true });
      return;
    }
    nav(backTo());
  }

  async function load() {
    const session = readPlay();
    const playing = Boolean(session?.ids?.length);
    const data = await api.photo(id, {
      ...(personId ? { person_id: personId } : {}),
      ...(tagFilter ? { tag: tagFilter } : {}),
      lite: 1,
    });
    photoNow.current = data;
    setPhoto(data);
    setCommentDraft(data.comment || "");
    setCommentSaved(false);
    setFaceNotes({});
    setFaceNoteOpen(null);
    const firstUnknown = (data.faces || []).find(
      (f) => !f.person_id && f.quality === "ok" && f.assigned_how !== "junk",
    );
    const nextId = (firstUnknown || data.faces?.[0] || {}).id || null;
    setActive(nextId);
    if (playing) prefetchPlay(session, id);
    if (nextId && !playing) {
      window.setTimeout(() => revealFaceCard(nextId), 50);
    }
    if (sharpenWanted.current) {
      sharpenWanted.current = false;
      window.setTimeout(() => runSharpen(), 0);
    }
    if (imagineWanted.current) {
      imagineWanted.current = false;
      window.setTimeout(() => openImagine(), 0);
    }
    const requested = id;
    api.imagineStatus(requested)
      .then((info) => {
        if (String(requested) !== String(idNow.current)) return;
        if (!info?.exists || !info.url) return;
        setImaginedUrl(`${info.url}?t=${Date.now()}`);
        if (info.prompt) setImaginePrompt((cur) => cur || info.prompt);
      })
      .catch(() => {});
    return data;
  }

  async function loadCatalog() {
    const next = await load();
    if (next) emitPhotoChange(next);
    emitCatalogChange();
    return next;
  }

  function revealFaceCard(faceId) {
    const card = document.getElementById(`face-card-${faceId}`);
    card?.scrollIntoView({ block: "nearest" });
  }

  function selectFace(faceId) {
    setActive(faceId);
    window.setTimeout(() => revealFaceCard(faceId), 0);
  }

  useEffect(() => {
    pageLive.current = true;
    return () => {
      pageLive.current = false;
    };
  }, []);

  useEffect(() => {
    setRematching(false);
    suggestionsFor.current = new Set();
    pickingGen.current += 1;
    pickingNow.current = false;
    pickingBusyNow.current = false;
    setPickingFace(false);
    setPickingBusy(false);
    load();
    setZoom(ZOOM_FIT);
    setPan({ x: 0, y: 0 });
    setUndoRematch(readRematchUndo(id));
    setCommentOpen(false);
    const albumHash = folderHashFrom(safeFrom(loc.state?.from));
    if (albumHash && id) {
      writeAlbumPos({ hash: albumHash, photoId: Number(id) });
    }
    rememberPersonPos(id);
    let cancelled = false;
    api
      .matchPhotoStatus(id)
      .then((st) => {
        if (cancelled || Number(idNow.current) !== Number(id)) return;
        if (st.status === "running") watchMatchRef.current?.(id, { start: st });
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [id, personId, tagFilter]);

  useEffect(() => {
    const albumHash = folderHashFrom(safeFrom(loc.state?.from));
    if (!albumHash || !id) return;
    const prev = readAlbumPos() || {};
    writeAlbumPos({
      hash: albumHash,
      photoId: Number(id),
      count: Math.max(Number(prev.count) || 0, Number(photo?.photo_index) || 0),
    });
  }, [id, photo?.photo_index, loc.state?.from]);

  const lastLoggedErr = useRef("");
  useEffect(() => {
    if (!err) {
      lastLoggedErr.current = "";
      return;
    }
    if (err === lastLoggedErr.current) return;
    lastLoggedErr.current = err;
    api.reportError(err, { page: "photo", photo_id: Number(id) || null }).catch(() => {});
  }, [err, id]);

  useEffect(() => {
    let cancelled = false;
    const cached = loadCachedPeople("");
    if (cached.length) setPeople(cached);
    const loadLite = () =>
      api.people(undefined, { lite: 1 }).then((listed) => {
        if (cancelled) return;
        const items = listed.items || [];
        setPeople(items);
        saveCachedPeople("", items);
      });
    api
      .people(undefined, { names: 1 })
      .then((listed) => {
        if (cancelled) return;
        const items = listed.items || [];
        if (items.length) setPeople((cur) => mergePeopleKeepCovers(items, cur));
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) loadLite().catch(() => {});
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    zoomRef.current = zoom;
  }, [zoom]);

  useEffect(() => {
    if (!id) return undefined;
    const thumb = photoImgUrl(id, "thumb");
    const view = photoImgUrl(id, "view");
    setPhotoSrc(thumb);
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      setPhotoSrc(view);
    };
    img.src = view;
    return () => {
      cancelled = true;
    };
  }, [id]);

  useEffect(() => {
    if (!photo || zoom < 2 || photo.file_available === false || !photo.file_url) return undefined;
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (!cancelled) setPhotoSrc(photo.file_url);
    };
    img.src = photo.file_url;
    return () => {
      cancelled = true;
    };
  }, [photo?.id, photo?.file_url, photo?.file_available, zoom]);

  useEffect(() => {
    if (!photo) return undefined;
    const ids = [photo.prev_id, photo.next_id].filter(Boolean);
    for (const nid of ids) {
      const thumb = new Image();
      thumb.src = `/api/photos/${nid}/thumb`;
      const view = new Image();
      view.src = `/api/photos/${nid}/view`;
    }
    return undefined;
  }, [photo?.id, photo?.prev_id, photo?.next_id]);

  useEffect(() => {
    if (!photo || !active) return undefined;
    const face = (photo.faces || []).find((f) => f.id === active);
    if (!face || face.person_id || face.assigned_how === "junk") return undefined;
    if (suggestionsFor.current.has(active)) return undefined;
    suggestionsFor.current.add(active);
    let cancelled = false;
    api
      .faceSuggestions(active)
      .then((data) => {
        if (cancelled) return;
        const items = data.items || [];
        setPhoto((cur) => {
          if (!cur || Number(cur.id) !== Number(photo.id)) return cur;
          return {
            ...cur,
            faces: (cur.faces || []).map((f) => (f.id === active ? { ...f, suggestions: items } : f)),
          };
        });
      })
      .catch(() => {
        suggestionsFor.current.delete(active);
      });
    return () => {
      cancelled = true;
    };
  }, [photo?.id, active]);

  useEffect(() => {
    function onChange(event) {
      const next = event.detail;
      if (!next || Number(next.id) !== Number(id)) return;
      if (next.hidden) {
        goBack();
        return;
      }
      setPhoto((cur) => (cur ? { ...cur, ...next } : cur));
      if (Object.prototype.hasOwnProperty.call(next, "comment")) {
        setCommentDraft(next.comment || "");
      }
    }
    window.addEventListener(PHOTO_CHANGE_EVENT, onChange);
    return () => window.removeEventListener(PHOTO_CHANGE_EVENT, onChange);
  }, [id]);

  useEffect(() => {
    if (!active) return;
    document.getElementById(`face-card-${active}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [active]);

  function placeZoomBar() {
    const fullEl = fullRef.current;
    const bar = zoomBarRef.current;
    if (!fullEl || !bar || !fullNow.current) return;
    const fw = fullEl.clientWidth;
    const fh = fullEl.clientHeight;
    if (fw < 80 || fh < 80) return;
    const rot = ((Number(photo?.rotation) || 0) % 360 + 360) % 360;
    const swapped = rot === 90 || rot === 270;
    const pw = Math.max(1, Number(swapped ? photo?.height : photo?.width) || 1);
    const ph = Math.max(1, Number(swapped ? photo?.width : photo?.height) || 1);
    const ar = pw / ph;
    const fitW = Math.min(fw, fh * ar);
    const fitH = Math.min(fh, fw / ar);
    const visW = fitW * ZOOM_FIT;
    const visH = fitH * ZOOM_FIT;
    const visRight = (fw + visW) / 2;
    const visBottom = (fh + visH) / 2;
    const tw = bar.offsetWidth || 168;
    const th = bar.offsetHeight || 40;
    const dock = 92;
    let x = visRight + 10;
    let y = visBottom - th - 8;
    if (x + tw > fw - 12) x = visRight - tw - 10;
    if (x + tw > fw - 12) x = fw - tw - 12;
    if (y + th > fh - dock) y = fh - th - dock;
    if (y < 72) y = 72;
    x = Math.max(12, x);
    bar.style.right = "auto";
    bar.style.bottom = "auto";
    bar.style.left = `${Math.round(x)}px`;
    bar.style.top = `${Math.round(y)}px`;
  }

  useEffect(() => {
    const el = full ? fullRef.current : stageRef.current;
    if (!el) return undefined;
    function applySize() {
      const box = el.getBoundingClientRect();
      const w = Number(photo?.width) || 1;
      const h = Number(photo?.height) || 1;
      el.style.setProperty("--stage-w", `${Math.max(0, Math.floor(box.width))}px`);
      el.style.setProperty("--stage-h", `${Math.max(0, Math.floor(box.height))}px`);
      el.style.setProperty("--photo-ar", String(w / h));
      placeZoomBar();
    }
    applySize();
    const id = window.requestAnimationFrame(placeZoomBar);
    const ro = new ResizeObserver(applySize);
    ro.observe(el);
    return () => {
      window.cancelAnimationFrame(id);
      ro.disconnect();
    };
  }, [photo?.id, photo?.width, photo?.height, photo?.rotation, full]);

  function resetZoom() {
    zoomNow.current = ZOOM_FIT;
    panNow.current = { x: 0, y: 0 };
    setZoom(ZOOM_FIT);
    setPan({ x: 0, y: 0 });
  }

  function setZoomAt(next, origin) {
    const z0 = zoomNow.current;
    const z1 = clamp(Number(Number(next).toFixed(3)), ZOOM_MIN, ZOOM_MAX);
    if (z1 === z0) {
      if (z1 <= ZOOM_FIT && (panNow.current.x || panNow.current.y)) {
        panNow.current = { x: 0, y: 0 };
        setPan({ x: 0, y: 0 });
      }
      return;
    }
    if (z1 <= ZOOM_FIT) {
      zoomNow.current = z1;
      panNow.current = { x: 0, y: 0 };
      setZoom(z1);
      setPan({ x: 0, y: 0 });
      return;
    }
    const p = panNow.current;
    const ox = origin?.x ?? 0;
    const oy = origin?.y ?? 0;
    const ratio = z1 / z0;
    const nextPan = { x: ox - (ox - p.x) * ratio, y: oy - (oy - p.y) * ratio };
    zoomNow.current = z1;
    panNow.current = nextPan;
    setZoom(z1);
    setPan(nextPan);
  }

  function nudgeZoom(dir, origin) {
    setZoomAt(zoomNow.current + dir * ZOOM_STEP, origin);
  }

  function zoomTo(level, origin) {
    const next = clamp(level, ZOOM_MIN, ZOOM_MAX);
    if (next <= ZOOM_FIT) {
      resetZoom();
      return;
    }
    setZoomAt(next, origin);
  }

  function cycleZoom(origin) {
    const z = zoomNow.current;
    const next = ZOOM_PRESETS.find((preset) => preset > z + 0.04) || ZOOM_FIT;
    zoomTo(next, origin);
  }

  function clickResetsZoom() {
    if (isFitZoom(zoomNow.current)) return false;
    resetZoom();
    return true;
  }

  function onPanStart(e) {
    if (pickingNow.current) return;
    if (e.button != null && e.button !== 0) return;
    if (e.target.closest?.(".nametag, .photo-full-tools, .photo-full-nav, .photo-full-east, .photo-full-zoombar, .photo-full-dock, .back-btn, .app-brand, .photo-imagine-pop, .photo-sharpen-badge, .photo-tag-row, .photo-full-tags")) {
      return;
    }
    if (zoomNow.current <= ZOOM_FIT) return;
    e.preventDefault();
    const p = panNow.current;
    didDrag.current = false;
    drag.current = { x: e.clientX, y: e.clientY, px: p.x, py: p.y, moved: false };
    e.currentTarget.setPointerCapture?.(e.pointerId);
  }

  function onPanMove(e) {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (Math.hypot(dx, dy) > 4) {
      d.moved = true;
      didDrag.current = true;
    }
    setPan({ x: d.px + dx, y: d.py + dy });
  }

  function onPanEnd(e) {
    drag.current = null;
    if (e?.currentTarget && e.pointerId != null) {
      try {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      } catch {
        /* already released */
      }
    }
  }

  function resetToolsPos() {
    toolsDrag.current = null;
    toolsPosNow.current = null;
    toolsSaved.current = null;
    setToolsPos(null);
    setToolsDragging(false);
    writeToolsPos(null);
  }

  function onToolsPointerDown(e) {
    e.stopPropagation();
    if (e.button != null && e.button !== 0) return;
    if (e.target.closest("button, a, input, textarea, select, label")) return;
    const el = toolsRef.current;
    if (!el) return;
    e.preventDefault();
    const rect = el.getBoundingClientRect();
    toolsDrag.current = {
      pointerId: e.pointerId,
      x: e.clientX,
      y: e.clientY,
      left: rect.left,
      top: rect.top,
      moved: false,
    };
    setToolsDragging(true);
    el.setPointerCapture?.(e.pointerId);
  }

  function onToolsPointerMove(e) {
    const d = toolsDrag.current;
    if (!d || d.pointerId !== e.pointerId) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!d.moved && Math.hypot(dx, dy) < 3) return;
    d.moved = true;
    const origin = toolsRef.current?.offsetParent?.getBoundingClientRect();
    const next = clampToolsPos(
      d.left + dx - (origin?.left || 0),
      d.top + dy - (origin?.top || 0),
      toolsRef.current,
    );
    toolsPosNow.current = next;
    setToolsPos(next);
  }

  function onToolsPointerUp(e) {
    const d = toolsDrag.current;
    if (!d || (e.pointerId != null && d.pointerId !== e.pointerId)) return;
    toolsDrag.current = null;
    setToolsDragging(false);
    if (e?.currentTarget && e.pointerId != null) {
      try {
        e.currentTarget.releasePointerCapture?.(e.pointerId);
      } catch {
        /* already released */
      }
    }
    if (d.moved && toolsPosNow.current) {
      toolsSaved.current = toolsPosNow.current;
      writeToolsPos(toolsPosNow.current);
    }
  }

  useEffect(() => {
    if (loc.state?.comment) {
      setFull(false);
      setCommentOpen(true);
      stayNamed.current = true;
      const from = safeFrom(loc.state?.from);
      nav(`${loc.pathname}${loc.search}`, { replace: true, state: from ? { from } : {} });
      window.setTimeout(() => commentRef.current?.focus(), 50);
      return;
    }
    if (loc.state?.sharpen) {
      sharpenWanted.current = true;
      stayNamed.current = true;
      const from = safeFrom(loc.state?.from);
      nav(`${loc.pathname}${loc.search}`, { replace: true, state: from ? { from } : {} });
      window.setTimeout(() => runSharpen(), 0);
      return;
    }
    if (loc.state?.imagine) {
      imagineWanted.current = true;
      stayNamed.current = true;
      setFull(false);
      setImagineOpen(true);
      const from = safeFrom(loc.state?.from);
      nav(`${loc.pathname}${loc.search}`, { replace: true, state: from ? { from } : {} });
      return;
    }
    if (loc.state?.markFace) {
      stayNamed.current = true;
      setFull(true);
      beginMarkFace();
      const from = safeFrom(loc.state?.from);
      nav(`${loc.pathname}${loc.search}`, { replace: true, state: from ? { from } : {} });
      return;
    }
    if (loc.state?.fullscreen) {
      setFull(true);
      const from = safeFrom(loc.state?.from);
      nav(`${loc.pathname}${loc.search}`, { replace: true, state: from ? { from } : {} });
    }
  }, [loc.pathname, loc.search, loc.state]);

  useEffect(() => {
    document.body.style.overflow = full ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [full]);

  useEffect(() => {
    const sync = () => setFullLabels(readFullscreenLabels());
    window.addEventListener(FULLSCREEN_LABELS_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(FULLSCREEN_LABELS_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(() => {
    const sync = () => setPlay(readPlay());
    window.addEventListener(PLAY_EVENT, sync);
    return () => window.removeEventListener(PLAY_EVENT, sync);
  }, []);

  useEffect(() => {
    let inBrowserFs = false;
    function onFs() {
      const on = Boolean(document.fullscreenElement || document.webkitFullscreenElement);
      if (inBrowserFs && !on && readPlay()?.playing) {
        updatePlay({ playing: false });
        setPlay(readPlay());
      }
      inBrowserFs = on;
    }
    document.addEventListener("fullscreenchange", onFs);
    document.addEventListener("webkitfullscreenchange", onFs);
    return () => {
      document.removeEventListener("fullscreenchange", onFs);
      document.removeEventListener("webkitfullscreenchange", onFs);
    };
  }, []);

  useEffect(() => {
    if (!full) return undefined;
    const el = toolsRef.current;
    if (!el) return undefined;
    function keepOnScreen() {
      if (toolsDrag.current) return;
      const saved = toolsSaved.current;
      if (!saved) return;
      const next = clampToolsPos(saved.x, saved.y, el);
      const pos = toolsPosNow.current;
      if (pos && next.x === pos.x && next.y === pos.y) return;
      toolsPosNow.current = next;
      setToolsPos(next);
    }
    keepOnScreen();
    const ro = new ResizeObserver(keepOnScreen);
    ro.observe(el);
    window.addEventListener("resize", keepOnScreen);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", keepOnScreen);
    };
  }, [full, photo?.id]);

  useEffect(() => {
    setSharpening(false);
    sharpeningNow.current = false;
    setSharpenedUrl("");
    setShowSharpened(false);
    setImagining(false);
    imaginingNow.current = false;
    setImaginedUrl("");
    setShowImagined(false);
    setImagineOpen(false);
    setImaginePrompt("");
    const session = readPlay();
    const idx = playIndexOf(session, id);
    if (idx >= 0) {
      setFull(true);
      if (idx !== session.index) updatePlay({ index: idx });
      stayNamed.current = false;
      if (session.playing) enterBrowserFullscreen();
      return;
    }
    if (stayNamed.current) {
      stayNamed.current = false;
      return;
    }
    if (loc.state?.comment) {
      setFull(false);
      setCommentOpen(true);
      return;
    }
    if (loc.state?.imagine) {
      setFull(false);
      setImagineOpen(true);
      return;
    }
    setCommentOpen(false);
    setFull(true);
  }, [id]);

  useEffect(() => {
    if (!full) return undefined;
    const node = fullRef.current;
    if (!node) return undefined;
    const t = window.setTimeout(() => node.focus?.({ preventScroll: true }), 0);
    return () => window.clearTimeout(t);
  }, [full, id]);

  useEffect(() => {
    return () => {
      window.setTimeout(() => {
        if (advancing.current) return;
        if (/^\/photos\/\d+/.test(window.location.pathname)) return;
        stopPlay();
      }, 0);
    };
  }, []);

  useEffect(() => {
    if (!full || !play?.playing || playIndex() < 0 || !isFitZoom(zoom)) return undefined;
    const ms = readPlayIntervalMs();
    const timer = window.setTimeout(() => goPlay(1), ms);
    return () => window.clearTimeout(timer);
  }, [id, full, play?.playing, play?.intervalMs, play?.ids, zoom]);

  useEffect(() => {
    const nodes = [stageRef.current, fullRef.current].filter(Boolean);
    if (!nodes.length) return undefined;
    function measure() {
      for (const el of nodes) {
        el.style.setProperty("--stage-h", `${el.clientHeight}px`);
        el.style.setProperty("--stage-w", `${el.clientWidth}px`);
      }
    }
    measure();
    const ro = new ResizeObserver(measure);
    nodes.forEach((el) => ro.observe(el));
    return () => ro.disconnect();
  }, [photo, full]);

  useEffect(() => {
    const pinch = { zoom: ZOOM_FIT, x: 0, y: 0, active: false };
    function applyWheel(e, host) {
      e.preventDefault();
      e.stopPropagation();
      if (pinch.active) return;
      setZoomAt(zoomNow.current * wheelZoomFactor(e), eventOrigin(e, host));
    }
    function onFullWheel(e) {
      if (!fullNow.current) return;
      if (e.target.closest?.("input, textarea, .nav, .photo-menu")) return;
      const openMenu = document.querySelector(".photo-menu");
      if (openMenu) {
        const r = openMenu.getBoundingClientRect();
        if (e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom) return;
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      applyWheel(e, fullRef.current);
    }
    function onStageWheel(e) {
      if (fullNow.current) return;
      applyWheel(e, e.currentTarget);
    }
    function onGestureStart(e) {
      e.preventDefault();
      const origin = eventOrigin(e, e.currentTarget);
      pinch.active = true;
      pinch.zoom = zoomNow.current;
      pinch.x = origin.x;
      pinch.y = origin.y;
    }
    function onGestureChange(e) {
      e.preventDefault();
      setZoomAt(pinch.zoom * (e.scale || 1), { x: pinch.x, y: pinch.y });
    }
    function onGestureEnd(e) {
      e.preventDefault();
      pinch.active = false;
    }
    const stage = stageRef.current;
    const overlay = fullRef.current;
    if (full) window.addEventListener("wheel", onFullWheel, { passive: false, capture: true });
    else if (stage) stage.addEventListener("wheel", onStageWheel, { passive: false });
    const gestureHost = full ? overlay : stage;
    if (gestureHost) {
      gestureHost.addEventListener("gesturestart", onGestureStart, { passive: false });
      gestureHost.addEventListener("gesturechange", onGestureChange, { passive: false });
      gestureHost.addEventListener("gestureend", onGestureEnd, { passive: false });
    }
    return () => {
      window.removeEventListener("wheel", onFullWheel, { capture: true });
      if (stage) stage.removeEventListener("wheel", onStageWheel);
      if (gestureHost) {
        gestureHost.removeEventListener("gesturestart", onGestureStart);
        gestureHost.removeEventListener("gesturechange", onGestureChange);
        gestureHost.removeEventListener("gestureend", onGestureEnd);
      }
    };
  }, [full, photo?.id]);

  useEffect(() => {
    function onKey(e) {
      if (!photo) return;
      const undoKey =
        (e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && String(e.key || "").toLowerCase() === "z";
      if (undoKey) {
        if (e.target.matches?.("textarea, [contenteditable]")) return;
        if (e.repeat) {
          e.preventDefault();
          e.stopPropagation();
          return;
        }
        if (!peekUndo() && !undoBusy.current && undoWanted.current <= 0) return;
        e.preventDefault();
        e.stopPropagation();
        undoWanted.current += 1;
        undoLastRef.current();
        return;
      }
      const photoArrow =
        (e.key === "ArrowLeft" || e.key === "ArrowRight") &&
        !e.altKey &&
        !e.metaKey &&
        !e.ctrlKey &&
        !e.shiftKey;
      if (fullNow.current && photoArrow) {
        const typingInOverlay = Boolean(
          e.target.closest?.(".photo-full") && e.target.closest?.("input, textarea, [contenteditable]"),
        );
        if (!typingInOverlay) {
          e.preventDefault();
          e.stopPropagation();
          if (pickingNow.current) cancelMarkFaceRef.current();
          if (e.target.closest?.("input, textarea, [contenteditable]")) e.target.blur?.();
          goPlay(e.key === "ArrowRight" ? 1 : -1);
          return;
        }
      }
      if (pickingNow.current) {
        if (e.key === "Escape") {
          e.preventDefault();
          e.stopPropagation();
          cancelMarkFaceRef.current();
        }
        return;
      }
      const zoomInKey = e.key === "+" || e.key === "=" || e.code === "NumpadAdd";
      const zoomOutKey = e.key === "-" || e.key === "_" || e.code === "Minus" || e.code === "NumpadSubtract";
      const typing = e.target.matches?.("input, textarea, [contenteditable]");
      const typingInFullOverlay = Boolean(
        typing && e.target.closest?.(".photo-full input, .photo-full textarea, .photo-imagine-pop, .photo-full-comment"),
      );
      if (fullNow.current && !e.metaKey && !e.ctrlKey && !e.altKey && !typingInFullOverlay) {
        if (zoomInKey || zoomOutKey || e.key === "0") {
          e.preventDefault();
          e.stopPropagation();
          if (typing) e.target.blur?.();
          if (zoomInKey) nudgeZoom(1);
          else if (zoomOutKey) nudgeZoom(-1);
          else resetZoom();
          return;
        }
      }
      if (typing) {
        if (commentOpen && e.key === "Escape" && !fullNow.current) {
          e.preventDefault();
          setCommentOpen(false);
        }
        if (imagineOpen && e.key === "Escape") {
          e.preventDefault();
          if (!imaginingNow.current) setImagineOpen(false);
        }
        const inFaceName = e.target.matches?.("input") && e.target.closest?.(".face-edit");
        if (!(inFaceName && photoArrow)) return;
        e.preventDefault();
        e.target.blur?.();
      }
      if (commentOpen && e.key === "Escape" && !fullNow.current) {
        e.preventDefault();
        setCommentOpen(false);
        return;
      }
      if (imagineOpen && e.key === "Escape") {
        e.preventDefault();
        if (!imaginingNow.current) setImagineOpen(false);
        return;
      }
      if (fullNow.current) {
        if (e.metaKey || e.ctrlKey || e.altKey) return;
        e.preventDefault();
        e.stopPropagation();
        if (e.key === "ArrowRight") {
          goPlay(1);
          return;
        }
        if (e.key === "ArrowLeft") {
          goPlay(-1);
          return;
        }
        if (e.key === "+" || e.key === "=" || e.code === "NumpadAdd") {
          nudgeZoom(1);
          return;
        }
        if (e.key === "-" || e.key === "_" || e.code === "Minus" || e.code === "NumpadSubtract") {
          nudgeZoom(-1);
          return;
        }
        if (e.key === "0") {
          resetZoom();
          return;
        }
        if (e.key === "l" || e.key === "L") {
          setFullLabels(applyFullscreenLabels(!readFullscreenLabels()));
          return;
        }
        if (e.key === " " || e.key === "Spacebar") {
          if (readPlay()) togglePlay();
          return;
        }
        if (e.key === "Escape" || !readPlay()) {
          endPlay();
        }
        return;
      }
      const faces = photo.faces || [];
      const idx = faces.findIndex((f) => f.id === active);
      if (e.key === "ArrowRight") goPhoto(photo.next_id);
      if (e.key === "ArrowLeft") goPhoto(photo.prev_id);
      if (e.key === "+" || e.key === "=" || e.code === "NumpadAdd") nudgeZoom(1);
      if (e.key === "-" || e.key === "_" || e.code === "Minus" || e.code === "NumpadSubtract") nudgeZoom(-1);
      if (e.key === "0") resetZoom();
      if (e.key === "l" || e.key === "L") setFullLabels(applyFullscreenLabels(!readFullscreenLabels()));
      if (e.key === "j") setActive(faces[Math.min(faces.length - 1, idx + 1)]?.id);
      if (e.key === "k") setActive(faces[Math.max(0, idx - 1)]?.id);
      if (e.key === "n") {
        const typed = window.prompt("New person name");
        if (typed) assign({ name: typed });
      }
      if (e.key === "u" && active) removeName(active);
      const num = Number(e.key);
      const face = faces.find((f) => f.id === active);
      if (num >= 1 && num <= 5 && face?.suggestions?.[num - 1]) {
        assign({ person_id: face.suggestions[num - 1].person_id });
      }
    }
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [photo, active, personId, tagFilter, full, play, id, commentOpen, imagineOpen]);

  function rememberPerson(person) {
    if (!person?.id || !person.name) return;
    setPeople((cur) => {
      const i = cur.findIndex((p) => String(p.id) === String(person.id));
      if (i < 0) return [...cur, person];
      const next = cur.slice();
      next[i] = { ...cur[i], ...person };
      return next;
    });
  }

  function catalogHits(face, typed) {
    return matchPeople(typed, people, { excludeId: face?.person_id });
  }

  function pickCatalogName(face, typed, { useHighlight = false } = {}) {
    const hits = catalogHits(face, typed);
    if (useHighlight && namePick >= 0 && hits[namePick]) return hits[namePick];
    return uniqueFirstName(typed, people, { excludeId: face?.person_id });
  }

  function applyCatalogPerson(person, face) {
    if (!person || !face) return;
    setDrafts((cur) => ({ ...cur, [face.id]: person.name }));
    setNamePick(-1);
    rememberPerson(person);
    assign({ person_id: person.id }, face.id);
  }

  async function moveTag(face, pos) {
    if (!face) return;
    rememberFaceChange(face.id, "moving the name");
    paintFace(face.id, pos ? { tag_x: pos.left, tag_y: pos.top } : { tag_x: null, tag_y: null });
    try {
      await api.patchFace(face.id, pos ? { tag_x: pos.left, tag_y: pos.top } : { clear_tag: true });
    } catch {
      await load();
    }
  }

  function faceById(faceId) {
    return (photoNow.current?.faces || photo?.faces || []).find((f) => Number(f.id) === Number(faceId));
  }

  function rememberFaceChange(faceId, label) {
    pushFaceUndo(photo, faceById(faceId), label);
  }

  async function restoreFaceSnapshot(faceId, before) {
    const cur = faceById(faceId);
    const nowJunk = cur?.assigned_how === "junk";
    const wasJunk = before.assigned_how === "junk";
    if (wasJunk && !nowJunk) {
      await api.junkFace(faceId);
    } else if (!wasJunk && nowJunk) {
      await api.restoreFace(faceId);
    }
    if (!wasJunk) {
      if (before.person_id) {
        await api.assignFace(faceId, { person_id: before.person_id });
      } else if (cur?.person_id) {
        await api.unassignFace(faceId);
      }
      if (String(before.comment || "") !== String(cur?.comment || "")) {
        await api.patchFace(faceId, { comment: before.comment || "" });
      }
      const tagSame = before.tag_x == null && cur?.tag_x == null && before.tag_y == null && cur?.tag_y == null;
      if (!tagSame && (before.tag_x !== cur?.tag_x || before.tag_y !== cur?.tag_y)) {
        if (before.tag_x == null || before.tag_y == null) {
          await api.patchFace(faceId, { clear_tag: true });
        } else {
          await api.patchFace(faceId, { tag_x: before.tag_x, tag_y: before.tag_y });
        }
      }
    }
    await loadCatalog();
  }

  function undoAgainHint() {
    if (undoWanted.current > 0 || undoCount() > 0) {
      return " Press again to undo the previous change.";
    }
    return "";
  }

  async function applyUndoEntry(entry) {
    const live = photoNow.current;
    if (entry.type === "rematch") {
      const result = await api.undoMatchPhoto(entry.photoId, entry.faceIds);
      if (Number(entry.photoId) === Number(live?.id)) {
        clearRematchUndo(live.id);
        setUndoRematch(null);
        await loadCatalog();
      }
      const n = Number(result.undone) || 0;
      setNote(
        n
          ? `Undid ${n} name${n === 1 ? "" : "s"}.${undoAgainHint()}`
          : "Nothing left to undo.",
      );
      return;
    }
    if (entry.type === "photo-comment") {
      const result = await api.patchPhoto(entry.photoId, { comment: entry.before || "" });
      if (Number(entry.photoId) === Number(live?.id)) {
        setPhoto((cur) => (cur ? { ...cur, comment: result.comment || "" } : cur));
        setCommentDraft(result.comment || "");
      }
      setNote(`Undid the photo comment.${undoAgainHint()}`);
      return;
    }
    if (entry.type === "face") {
      await restoreFaceSnapshot(entry.faceId, entry.before);
      setNote(
        entry.label ? `Undid ${entry.label}.${undoAgainHint()}` : `Undid the last change.${undoAgainHint()}`,
      );
      if (entry.faceId) selectFace(entry.faceId);
      return;
    }
    setNote("Nothing to undo.");
  }

  async function undoLastEdit() {
    if (undoBusy.current) return false;
    undoBusy.current = true;
    setUndoing(true);
    setErr("");
    let did = false;
    try {
      while (undoWanted.current > 0) {
        const entry = peekUndo();
        if (!entry) {
          undoWanted.current = 0;
          if (!did) setNote("Nothing to undo.");
          break;
        }
        popUndo();
        undoWanted.current -= 1;
        await applyUndoEntry(entry);
        did = true;
      }
    } catch (ex) {
      undoWanted.current = 0;
      setNote("");
      setErr(ex.message || "Could not undo.");
      await load();
    } finally {
      undoBusy.current = false;
      setUndoing(false);
      if (undoWanted.current > 0) undoLastRef.current();
    }
    return did;
  }
  undoLastRef.current = undoLastEdit;

  function paintFace(faceId, patch) {
    setPhoto((cur) => {
      if (!cur) return cur;
      return {
        ...cur,
        faces: (cur.faces || []).map((f) => (Number(f.id) === Number(faceId) ? { ...f, ...patch } : f)),
      };
    });
    const cur = photo;
    if (cur) {
      emitPhotoChange({
        ...cur,
        faces: (cur.faces || []).map((f) => (Number(f.id) === Number(faceId) ? { ...f, ...patch } : f)),
      });
    }
    emitCatalogChange();
    setChangingId(null);
    setDrafts((cur) => {
      const next = { ...cur };
      delete next[faceId];
      return next;
    });
  }

  async function assign(body, faceId = active) {
    if (!faceId) return;
    rememberFaceChange(faceId, "the name");
    const picked = body.person_id
      ? people.find((p) => String(p.id) === String(body.person_id))
      : null;
    const personName = (picked?.name || body.name || "").trim();
    if (picked) rememberPerson(picked);
    setErr("");
    setNote(personName ? `Saved ${personName}.` : "Saved.");
    setSavedId(faceId);
    if (personName || body.person_id) {
      paintFace(faceId, {
        person_id: body.person_id || picked?.id || null,
        person_name: personName || null,
        assigned_how: "manual",
      });
    }
    try {
      const result = await api.assignFace(faceId, body);
      if (result?.person_id) {
        paintFace(faceId, {
          person_id: result.person_id,
          person_name: personName || null,
          assigned_how: "manual",
        });
      }
      setName("");
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not save the name.");
      await load();
    }
  }

  function draftName(face) {
    if (Object.prototype.hasOwnProperty.call(drafts, face.id)) return drafts[face.id];
    return face.person_name || "";
  }

  async function saveFaceName(face) {
    const next = draftName(face).trim();
    if (!next || savingId) return;
    const catalog = pickCatalogName(face, next);
    if (catalog && catalog.name.trim().toLowerCase() !== next.toLowerCase()) {
      applyCatalogPerson(catalog, face);
      return;
    }
    const same = face.person_id && next === (face.person_name || "").trim();
    if (same) {
      setErr("");
      setNote(`Saved ${next}.`);
      setSavedId(face.id);
      if (face.assigned_how === "auto" || face.assigned_how === "sidecar") {
        try {
          await api.confirmFace(face.id);
          paintFace(face.id, { assigned_how: "manual" });
        } catch (ex) {
          setNote("");
          setErr(ex.message || "Could not save the name.");
        }
      }
      return;
    }
    rememberFaceChange(face.id, "the name");
    setErr("");
    setNote(`Saved ${next}.`);
    setSavingId(face.id);
    try {
      const match = (people || []).find(
        (p) => !p.unknown_name && String(p.name || "").trim().toLowerCase() === next.toLowerCase(),
      );
      const category = categories[face.id] || "";
      paintFace(face.id, {
        person_id: match?.id || face.person_id || null,
        person_name: next,
        assigned_how: "manual",
      });
      let result = null;
      if (match && String(match.id) !== String(face.person_id || "")) {
        result = await api.assignFace(face.id, { person_id: match.id, category });
      } else if (face.person_id) {
        await api.patchPerson(face.person_id, { name: next, ...(category ? { category } : {}) });
      } else {
        result = await api.assignFace(face.id, { name: next, category });
      }
      if (result?.person_id) {
        paintFace(face.id, {
          person_id: result.person_id,
          person_name: next,
          assigned_how: "manual",
        });
        rememberPerson({ id: result.person_id, name: next });
      }
      setSavedId(face.id);
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not save the name.");
      await load();
    } finally {
      setSavingId(null);
    }
  }

  async function markNotPerson(faceId) {
    if (!faceId) return;
    rememberFaceChange(faceId, "hiding this face");
    setErr("");
    setNote("Hiding this as not a person…");
    paintFace(faceId, { person_id: null, person_name: null, assigned_how: "junk", quality: "unidentifiable" });
    try {
      const result = await api.junkFace(faceId);
      const extra = result.also_ignored || 0;
      await loadCatalog();
      setNote(
        extra
          ? `Marked as not a person. Also hid ${extra} similar object${extra === 1 ? "" : "s"}.`
          : "Marked as not a person. Similar objects will be ignored later.",
      );
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not mark this as not a person.");
    }
  }

  async function restorePerson(faceId) {
    if (!faceId) return;
    rememberFaceChange(faceId, "restoring this face");
    setErr("");
    setNote("Restoring this face…");
    paintFace(faceId, { assigned_how: null, quality: "ok" });
    try {
      await api.restoreFace(faceId);
      await loadCatalog();
      selectFace(faceId);
      setNote("This face is a person again. You can type a name.");
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not restore this face.");
      await load();
    }
  }

  function faceNote(f) {
    if (Object.prototype.hasOwnProperty.call(faceNotes, f.id)) return faceNotes[f.id];
    return f.comment || "";
  }

  async function saveFaceComment(f) {
    if (!f || faceNoteSaving === f.id) return;
    const next = faceNote(f).trim();
    if (next === String(f.comment || "").trim()) return;
    rememberFaceChange(f.id, "the note");
    setFaceNoteSaving(f.id);
    setErr("");
    try {
      const result = await api.patchFace(f.id, { comment: next });
      setPhoto((cur) => {
        if (!cur) return cur;
        return {
          ...cur,
          faces: (cur.faces || []).map((item) =>
            item.id === f.id ? { ...item, comment: result.comment || "" } : item,
          ),
        };
      });
      setFaceNotes((cur) => {
        const nextDrafts = { ...cur };
        delete nextDrafts[f.id];
        return nextDrafts;
      });
      setFaceNoteOpen((cur) => (cur === f.id ? null : cur));
    } catch (ex) {
      setErr(ex.message || "Could not save the note.");
    } finally {
      setFaceNoteSaving(null);
    }
  }

  async function saveComment() {
    if (!photo || commentSaving) return;
    const next = commentDraft.trim();
    if (next === String(photo.comment || "").trim()) return;
    pushUndo({ type: "photo-comment", photoId: photo.id, before: String(photo.comment || ""), label: "the comment" });
    setCommentSaving(true);
    setErr("");
    try {
      const result = await api.patchPhoto(photo.id, { comment: next });
      setPhoto((cur) => (cur ? { ...cur, comment: result.comment || "" } : cur));
      setCommentDraft(result.comment || "");
      setCommentSaved(true);
      emitPhotoChange({ ...photo, comment: result.comment || "" });
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not save the comment.");
    } finally {
      setCommentSaving(false);
    }
  }

  function beginMarkFace() {
    if (pickingBusy) return;
    if (readPlay()?.playing) {
      updatePlay({ playing: false });
      setPlay(readPlay());
    }
    setErr("");
    setNote("Draw a box around the face to add.");
    setPickingFace(true);
    api.warmupFaces().catch(() => {});
  }

  function cancelMarkFace() {
    pickingGen.current += 1;
    pickingNow.current = false;
    pickingBusyNow.current = false;
    setPickingFace(false);
    setPickingBusy(false);
    setNote("");
    setErr("");
  }
  cancelMarkFaceRef.current = cancelMarkFace;

  function toggleMarkFace() {
    if (pickingFace) cancelMarkFace();
    else beginMarkFace();
  }

  async function addMissedFace(box) {
    if (!photo || pickingBusyNow.current) return;
    const gen = pickingGen.current;
    pickingBusyNow.current = true;
    setPickingBusy(true);
    setErr("");
    setNote("Finding the face…");
    try {
      const result = await api.addPhotoFace(photo.id, box);
      if (gen !== pickingGen.current) return;
      const face = result?.face;
      if (!face?.id) throw new Error("No face came back.");
      setPhoto((cur) => {
        if (!cur) return cur;
        const faces = cur.faces || [];
        const already = faces.some((f) => Number(f.id) === Number(face.id));
        const nextFaces = already
          ? faces.map((f) => (Number(f.id) === Number(face.id) ? { ...f, ...face } : f))
          : [...faces, face];
        return { ...cur, faces: nextFaces };
      });
      setPickingFace(false);
      setFull(false);
      exitBrowserFullscreen();
      stayNamed.current = true;
      selectFace(face.id);
      if (result.restored) {
        setNote("That face was hidden. Type the name.");
      } else if (result.existing) {
        setNote("That face was already on the photo. You can name it here.");
      } else if ((face.suggestions || []).length) {
        setNote("Face added. Pick a suggested name, or type one.");
      } else {
        setNote("Face added. Type the name.");
      }
    } catch (ex) {
      if (gen !== pickingGen.current) return;
      const msg = ex.message || "Could not pick up that face.";
      if (/larger box|too small|tighter/i.test(msg)) {
        setNote("Drag a new box around the head, a little tighter.");
      } else {
        setNote("");
      }
      setErr(msg);
    } finally {
      if (gen === pickingGen.current) {
        pickingBusyNow.current = false;
        setPickingBusy(false);
      }
    }
  }

  async function watchMatch(photoId, opts = {}) {
    rematchAbort.current?.abort();
    const ac = new AbortController();
    rematchAbort.current = ac;
    setRematching(true);
    setErr("");
    setNote("Re-identifying in the background. You can keep browsing.");
    try {
      const result = await api.waitMatchPhoto(photoId, { start: opts.start, signal: ac.signal });
      emitCatalogChange();
      if (ac.signal.aborted) return;
      if (!pageLive.current || Number(idNow.current) !== Number(photoId)) return;
      const next = await load();
      if (next) emitPhotoChange(next);
      const named = Number(result.auto_assigned) || 0;
      const leftover = Number(result.medium) || 0;
      const viaAda = Number(result.adaface_assigned) || 0;
      const found = Number(result.new_faces) || 0;
      const considered = Number(result.considered) || 0;
      const who = [...new Set((result.assigned || []).map((row) => row.name).filter(Boolean))];
      const faceIds = [...new Set((result.assigned || []).map((row) => Number(row.face_id)).filter((fid) => fid > 0))];
      const restoredNamed = opts.restoredFaceId
        ? (result.assigned || []).find((row) => Number(row.face_id) === Number(opts.restoredFaceId))
        : null;
      if (!opts.restoredFaceId) {
        const undo = named && faceIds.length ? writeRematchUndo(photoId, faceIds, who) : (clearRematchUndo(photoId), null);
        setUndoRematch(undo);
        if (named && faceIds.length) {
          pushUndo({ type: "rematch", photoId, faceIds, label: "re-identify" });
        }
      }
      if (restoredNamed?.name) {
        setNote(`This face is a person. Named ${restoredNamed.name}.`);
      } else if (named) {
        const whoText = who.length ? ` (${who.join(", ")})` : "";
        const adaText = viaAda ? ` ${viaAda} via AdaFace.` : "";
        setNote(
          leftover
            ? `Named ${named} face${named === 1 ? "" : "s"} from the catalog${whoText}.${adaText} Undo if they are wrong. ${leftover} still look close — check suggestions.`
            : `Named ${named} face${named === 1 ? "" : "s"} from the catalog${whoText}.${adaText} Undo if they are wrong.`,
        );
      } else if (leftover) {
        setNote("No sure matches yet. Unnamed faces have fresh suggestions from the nearest named photos.");
      } else if (found) {
        setNote(
          `Found ${found} extra face${found === 1 ? "" : "s"}. Name them on the right, or try Re-identify again.`,
        );
      } else if (considered) {
        setNote(
          `Checked ${considered} unnamed face${considered === 1 ? "" : "s"} against every named photo, nearby examples, and people already in this album. None were sure enough. Use Add a face if someone was missed.`,
        );
      } else if (opts.restoredFaceId) {
        setNote("This face is a person again. You can type a name.");
      } else {
        setNote("No unnamed faces to match. Use Add a face if someone was missed.");
      }
    } catch (ex) {
      if (ex.name === "AbortError" || ac.signal.aborted) return;
      if (!pageLive.current || Number(idNow.current) !== Number(photoId)) return;
      setNote("Re-identify is still running in the background. You can keep browsing.");
      setErr(ex.message || "Could not re-identify faces.");
    } finally {
      if (!ac.signal.aborted && pageLive.current && Number(idNow.current) === Number(photoId)) {
        setRematching(false);
      }
    }
  }
  watchMatchRef.current = watchMatch;

  async function rematchFaces() {
    if (!photo || rematching) return;
    await watchMatch(photo.id);
  }

  async function undoRematchFaces() {
    const ids = undoRematch?.faceIds || [];
    if (!photo || !ids.length || undoing) return;
    setUndoing(true);
    setErr("");
    try {
      const result = await api.undoMatchPhoto(photo.id, ids);
      clearRematchUndo(photo.id);
      setUndoRematch(null);
      const next = await load();
      if (next) emitPhotoChange(next);
      emitCatalogChange();
      const n = Number(result.undone) || 0;
      setNote(
        n
          ? `Undid ${n} name${n === 1 ? "" : "s"} from re-identify.`
          : "Nothing left to undo. Names you typed stay.",
      );
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not undo re-identify.");
    } finally {
      setUndoing(false);
    }
  }

  function openComment() {
    setFull(false);
    exitBrowserFullscreen();
    setCommentOpen(true);
    window.setTimeout(() => commentRef.current?.focus(), 50);
  }

  async function runSharpen() {
    if (sharpeningNow.current) return;
    if (sharpenedUrl) {
      setShowImagined(false);
      setShowSharpened(true);
      return;
    }
    sharpeningNow.current = true;
    setSharpening(true);
    setErr("");
    try {
      const result = await api.sharpenPhoto(id);
      const url = `${result.url || `/api/photos/${id}/sharpened`}?t=${Date.now()}`;
      setSharpenedUrl(url);
      setShowImagined(false);
      setShowSharpened(true);
    } catch (ex) {
      setShowSharpened(false);
      setErr(ex.message || "Could not sharpen this photo.");
    } finally {
      sharpeningNow.current = false;
      setSharpening(false);
    }
  }

  function toggleSharpen() {
    if (sharpening) return;
    if (showSharpened) {
      setShowSharpened(false);
      return;
    }
    if (sharpenedUrl) {
      setShowSharpened(true);
      return;
    }
    runSharpen();
  }

  function sharpenLabel(compact = false) {
    if (sharpening) return "Sharpening…";
    if (showSharpened) return compact ? "Original" : "Show original";
    if (sharpenedUrl) return compact ? "Sharpened" : "Show sharpened";
    return "Sharpen";
  }

  function openImagine() {
    if (readPlay()?.playing) {
      updatePlay({ playing: false });
      setPlay(readPlay());
    }
    if (!fullNow.current) {
      setFull(false);
      exitBrowserFullscreen();
    }
    setImagineOpen(true);
    stayNamed.current = true;
    window.setTimeout(() => {
      const el =
        document.getElementById("photo-imagine-full-prompt") ||
        document.getElementById("photo-imagine-prompt");
      el?.focus();
    }, 50);
  }

  async function runImagine() {
    if (imaginingNow.current) return;
    const prompt = imaginePrompt.trim();
    if (prompt.length < 3) {
      setErr("Describe the change in a few words.");
      return;
    }
    imaginingNow.current = true;
    setImagining(true);
    setErr("");
    setNote("Grok Imagine is changing this photo…");
    try {
      const result = await api.imaginePhoto(id, prompt);
      const url = `${result.url || `/api/photos/${id}/imagined`}?t=${Date.now()}`;
      setImaginedUrl(url);
      if (result.prompt) setImaginePrompt(result.prompt);
      setShowSharpened(false);
      setShowImagined(true);
      setImagineOpen(false);
      setNote("Temporary Grok preview. The original file is unchanged.");
    } catch (ex) {
      setShowImagined(false);
      setNote("");
      setErr(ex.message || "Could not change this photo.");
    } finally {
      imaginingNow.current = false;
      setImagining(false);
    }
  }

  function toggleImagine() {
    if (imagining) return;
    if (imagineOpen) {
      setImagineOpen(false);
      return;
    }
    if (showImagined) {
      setShowImagined(false);
      return;
    }
    if (imaginedUrl && !imagineOpen) {
      setShowSharpened(false);
      setShowImagined(true);
      return;
    }
    openImagine();
  }

  function imagineLabel(compact = false) {
    if (imagining) return "Changing…";
    if (showImagined) return compact ? "Original" : "Show original";
    if (imaginedUrl) return compact ? "Changed" : "Show changed";
    return compact ? "Grok" : "Change with Grok";
  }

  async function removeName(faceId) {
    if (!faceId) return;
    rememberFaceChange(faceId, "removing the name");
    setErr("");
    setNote("Removing name…");
    try {
      await api.unassignFace(faceId);
      await loadCatalog();
      setNote("Name removed from this face.");
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not remove the name.");
    }
  }

  async function removeAllNames() {
    const ids = (photo?.faces || []).filter((f) => f.person_id && f.assigned_how !== "junk").map((f) => f.id);
    if (!ids.length) return;
    ids.forEach((fid) => rememberFaceChange(fid, "removing the name"));
    setErr("");
    setNote("Removing names…");
    try {
      await api.unassignPhoto(photo.id);
      await loadCatalog();
      setNote(
        ids.length === 1 ? "Name removed from this face." : `Names removed from ${ids.length} faces on this photo.`,
      );
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not remove the names.");
    }
  }

  async function removeAllUnnamed() {
    const ids = (photo?.faces || [])
      .filter((f) => !f.person_id && f.assigned_how !== "junk")
      .map((f) => f.id);
    if (!ids.length) return;
    ids.forEach((fid) => rememberFaceChange(fid, "hiding this face"));
    setErr("");
    setNote("Hiding unnamed faces…");
    try {
      const result = await api.junkUnnamedPhoto(photo.id);
      await loadCatalog();
      const n = Number(result?.junked) || ids.length;
      setNote(
        n === 1 ? "Hid the unnamed face on this photo." : `Hid ${n} unnamed faces on this photo. Named people stay.`,
      );
    } catch (ex) {
      setNote("");
      setErr(ex.message || "Could not hide the unnamed faces.");
      await load();
    }
  }

  if (!photo) {
    const eager = photoSrc || photoImgUrl(id, "thumb");
    const eagerImg = eager ? (
      <img src={eager} alt="" fetchPriority="high" decoding="async" />
    ) : (
      <p className="photo-full-hint">Loading…</p>
    );
    if (full || readPlay()) {
      return (
        <div className="photo-full" role="dialog" aria-label="Loading photo">
          <div className="photo-full-zoom">{eagerImg}</div>
        </div>
      );
    }
    return eager ? <div className="viewer">{eagerImg}</div> : <p className="hint">Loading…</p>;
  }
  const taggedPhoto = {
    ...photo,
    faces: (photo.faces || []).map((f) => {
      if (!Object.prototype.hasOwnProperty.call(drafts, f.id)) return f;
      const typed = String(drafts[f.id] ?? "").trim();
      if (!typed) return f;
      return { ...f, person_name: typed };
    }),
  };
  const playSrc = photoSrc || photo.thumb_url || photo.view_url || photo.file_url;
  const liveSrc =
    showImagined && imaginedUrl
      ? imaginedUrl
      : showSharpened && sharpenedUrl
        ? sharpenedUrl
        : playSrc;
  const visibleFaces = overlayFaces(photo.faces || []);
  const hiddenFaces = displayFaces(photo.faces || []).filter((f) => f.assigned_how === "junk");
  const listedFaces = [...visibleFaces, ...hiddenFaces.filter((f) => !visibleFaces.some((v) => v.id === f.id))];
  const face = visibleFaces.find((f) => f.id === active) || listedFaces.find((f) => f.id === active) || visibleFaces[0] || null;
  const zoomedIn = zoom > ZOOM_FIT;
  const zoomedOut = zoom < ZOOM_FIT;
  const zoomClass = zoomedIn ? "zoomed" : zoomedOut ? "zoomed-out" : "";

  function openFace(f) {
    if (!f) return;
    setFull(false);
    selectFace(f.id);
  }

  const seq = photoSequence(photo);

  return (
    <div className="photo-page">
      <div className="page-head">
        <div className="photo-head-id">
          <div className="photo-head-nav">
            <BackButton
            to={backTo()}
            onClick={goBack}
            {...tip(
              String(loc.state?.from || "").startsWith("/to-name")
                ? "Return to Faces to name."
                : String(loc.state?.from || "").startsWith("/review")
                  ? "Return to Check names."
                  : personId
                    ? "Return to this person's photos."
                    : tagFilter
                      ? "Return to photos with this tag."
                      : "Return to Folder View.",
            )}
          />
          </div>
          <h1>{photo.filename}</h1>
          <p className="lede" {...tip("Keys: 1–5 save a suggested name, n new name, u remove name, j/k face, ←/→ photo.")}>
            {[
              photo.taken_at ? photo.taken_at.slice(0, 10) : "No date",
              seq?.label,
              `${visibleFaces.length} face(s)`,
            ]
              .filter(Boolean)
              .join(" · ")}
          </p>
          {note ? (
            <p className="save-note" role="status" aria-live="polite">
              {note}
            </p>
          ) : null}
          {err ? (
            <p className="error save-error" role="alert">
              {err}
            </p>
          ) : null}
          {photo.file_available === false ? (
            <p className="error">
              The original is offline. Showing the saved preview. Mount the NAS album in Finder to
              open the full photo.
            </p>
          ) : null}
        </div>
        <div className="photo-head-tools">
          <div className="photo-head-tools-row">
            <div className="photo-nav" role="group" aria-label="Photos">
              {photo.prev_id ? (
                <Link className="btn secondary" to={hrefFor(photo.prev_id)} state={photoNavState()} {...tip(personId ? "Previous photo of this person." : "Open the previous photo in the album.")}>
                  Previous
                </Link>
              ) : null}
              {seq ? (
                <span className="photo-seq" aria-live="polite">
                  {seq.index} of {seq.count}
                </span>
              ) : null}
              {photo.next_id ? (
                <Link className="btn secondary" to={hrefFor(photo.next_id)} state={photoNavState()} {...tip(personId ? "Next photo of this person." : "Open the next photo in the album.")}>
                  Next
                </Link>
              ) : null}
            </div>
            <div className="zoom-tools" role="group" aria-label="Zoom">
              <button
                type="button"
                className="secondary"
                disabled={zoom >= ZOOM_MAX}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  nudgeZoom(1);
                }}
                {...tip("Zoom in a step, up to 400%. Shortcut +")}
              >
                +
              </button>
              <button
                type="button"
                className="zoom-level"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  cycleZoom();
                }}
                {...tip("Cycle 75% → 100% → 200% → 400%. Shortcut 0 fits the photo at 75%.")}
              >
                {Math.round(zoom * 100)}%
              </button>
              <button
                type="button"
                className="secondary"
                disabled={zoom <= ZOOM_MIN}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  nudgeZoom(-1);
                }}
                {...tip("Zoom out. Shortcut −")}
              >
                −
              </button>
              <button
                type="button"
                className="secondary"
                aria-pressed={zoom >= ZOOM_MAX - 0.05}
                disabled={zoom >= ZOOM_MAX}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  zoomTo(ZOOM_MAX);
                }}
                {...tip("Zoom the photo to 400%.")}
              >
                400%
              </button>
            </div>
          </div>
          <div className="photo-head-actions" role="toolbar" aria-label="Photo">
            <NamesToggle />
            <LabelLayoutToggle />
            {(photo.faces || []).some((f) => f.person_id && f.assigned_how !== "junk") ? (
              <button
                type="button"
                className="secondary"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  removeAllNames();
                }}
                {...tip("Take every catalog name off this photo. Re-identify can try those faces again. The original file is unchanged.")}
              >
                Remove names
              </button>
            ) : null}
            {(photo.faces || []).some((f) => !f.person_id && f.assigned_how !== "junk") ? (
              <button
                type="button"
                className="secondary"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  removeAllUnnamed();
                }}
                {...tip("Hide every unnamed face on this photo as not a person. Named people stay. Undo puts them back. Other photos are not changed. The original file is unchanged.")}
              >
                Remove unnamed
              </button>
            ) : null}
            <button
              type="button"
              className="secondary"
              aria-pressed={pickingFace}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                toggleMarkFace();
              }}
              {...tip(
                pickingFace
                  ? pickingBusy
                    ? "Stop looking and stay on this photo. Shortcut Esc."
                    : "Cancel. Shortcut Esc."
                  : "Draw a box around a person the detector missed, then name them.",
              )}
            >
              {pickingFace ? "Cancel" : "Add a face"}
            </button>
            <button
              type="button"
              className="secondary"
              aria-pressed={showSharpened}
              disabled={sharpening || imagining}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                toggleSharpen();
              }}
              {...tip("Grok Imagine makes a sharper preview, with extra clarity on faces. The original file is never overwritten.")}
            >
              {sharpenLabel()}
            </button>
            <button
              type="button"
              className="secondary"
              aria-pressed={showImagined || imagineOpen}
              disabled={imagining || sharpening}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                toggleImagine();
              }}
              {...tip("Describe a change and Grok Imagine makes a preview. The original file is never overwritten.")}
            >
              {imagineLabel()}
            </button>
            <button
              type="button"
              className="secondary"
              disabled={rematching}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                rematchFaces();
              }}
              {...tip("Match unnamed faces to the catalog using the closest named photos, several examples of each person, and people already in this album. Names taken off this photo are tried again. Use Undo if a name is wrong.")}
            >
              {rematching ? "Re-identifying…" : "Re-identify faces"}
            </button>
            {undoRematch?.faceIds?.length ? (
              <button
                type="button"
                className="secondary"
                disabled={undoing || rematching}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  undoRematchFaces();
                }}
                {...tip("Remove the names Re-identify just applied on this photo. Names you typed stay.")}
              >
                {undoing
                  ? "Undoing…"
                  : undoRematch.faceIds.length === 1
                    ? "Undo name"
                    : `Undo ${undoRematch.faceIds.length} names`}
              </button>
            ) : null}
          </div>
        </div>
        <div className="photo-head-views">
          <ViewSwitch photoId={photo.id} personId={personId || undefined} />
        </div>
      </div>
      <div className="viewer">
        <div className="photo-stage-host">
          <div
            className={`stage ${zoomClass}`}
            ref={stageRef}
            onPointerDown={onPanStart}
            onPointerMove={onPanMove}
            onPointerUp={onPanEnd}
            onPointerCancel={onPanEnd}
            {...tip("Click the photo for fullscreen. Scroll, pinch, or + / − to zoom. Drag when zoomed in.")}
          >
            <div
              className="stage-zoom"
              style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
            >
              <LabeledPhoto
                photo={taggedPhoto}
                src={liveSrc || photoSrc || photo.thumb_url || photo.view_url || photo.file_url}
                fallbackSrc={photo.thumb_url}
                priority
                activeId={active}
                onFaceClick={(f) => openFace(f)}
                onPhotoClick={() => {
                  if (didDrag.current) return;
                  if (clickResetsZoom()) return;
                  setFull(true);
                }}
                overlayTags={fullLabels}
                showUnnamed
                movable
                onTagMove={moveTag}
                selecting={pickingFace}
                selectingBusy={pickingBusy}
                onRegionSelect={addMissedFace}
                fit
              />
            </div>
          </div>
          {showImagined ? (
            <button
              type="button"
              className="photo-sharpen-badge"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                openImagine();
              }}
              {...tip("Describe a different change. The original file stays untouched.")}
            >
              Changed with Grok · original unchanged
              {imaginePrompt ? ` · ${imaginePrompt}` : ""}
              {" · Change again"}
            </button>
          ) : showSharpened ? (
            <p className="photo-sharpen-badge">Temporary Grok preview · original unchanged</p>
          ) : null}
          <div className="photo-comment-wrap" onPointerDown={(e) => e.stopPropagation()}>
            {imagineOpen && !full ? (
              <ImaginePrompt
                value={imaginePrompt}
                onChange={setImaginePrompt}
                onSubmit={runImagine}
                onClose={() => !imagining && setImagineOpen(false)}
                busy={imagining}
              />
            ) : null}
            {commentOpen ? (
              <div className="photo-comment-pop" id="photo-comment-pop">
                <div className="people-search-head">
                  <label className="cluster-label" htmlFor="photo-comment">
                    Note
                  </label>
                  <button type="button" className="ghost" onClick={() => setCommentOpen(false)}>
                    Close
                  </button>
                </div>
                <textarea
                  id="photo-comment"
                  ref={commentRef}
                  rows={4}
                  maxLength={4000}
                  value={commentDraft}
                  placeholder="Add a note about this photo. It stays in the catalog, not on the file."
                  onChange={(e) => {
                    setCommentDraft(e.target.value);
                    setCommentSaved(false);
                  }}
                  onBlur={() => {
                    saveComment();
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Escape") {
                      e.preventDefault();
                      e.stopPropagation();
                      setCommentOpen(false);
                    }
                    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                      e.preventDefault();
                      e.stopPropagation();
                      saveComment();
                    }
                  }}
                  {...tip("Saved in the local catalog and the album sidecar, never written onto the original.")}
                />
                <button
                  type="button"
                  className={commentSaved ? "saved" : "secondary"}
                  disabled={commentSaving || commentDraft.trim() === String(photo.comment || "").trim()}
                  onClick={saveComment}
                  {...tip("Save this comment. Shortcut ⌘ Enter.")}
                >
                  {commentSaving ? "Saving…" : commentSaved ? "Saved" : "Save comment"}
                </button>
              </div>
            ) : (
              <button
                type="button"
                className={`photo-comment-chip${photo.comment ? " has-note" : ""}`}
                aria-expanded={false}
                aria-controls="photo-comment-pop"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  setCommentOpen(true);
                  window.setTimeout(() => commentRef.current?.focus(), 50);
                }}
                {...tip("A note about this picture. Saved in the local catalog, never written onto the original.")}
              >
                {photo.comment || "Add a note"}
              </button>
            )}
            <NamesToggle className="photo-labels-chip" />
          </div>
        </div>
        <aside>
          {listedFaces.map((f) => {
            const faceArea = Math.max(1, (f.x2 - f.x1) * (f.y2 - f.y1));
            const photoArea = Math.max(1, (photo.width || 1) * (photo.height || 1));
            const showCrop = listedFaces.length > 1 || faceArea / photoArea < 0.45;
            const mark = f.assigned_how === "junk" ? null : faceMark(f, visibleFaces);
            return (
            <div
              key={f.id}
              id={`face-card-${f.id}`}
              className={`face-row ${f.id === active ? "active" : ""}`}
              style={{ "--face-tone": faceTone(f, visibleFaces) }}
              onClick={() => selectFace(f.id)}
            >
              {mark ? <span className="face-tone-mark" aria-hidden="true">{mark}</span> : null}
              {f.assigned_how !== "junk" && showCrop ? (
                <div className="face-pic">
                  <img src={f.crop_url} alt="" />
                  <button
                    type="button"
                    className={`face-save${savedId === f.id ? " saved" : ""}`}
                    disabled={savingId === f.id || !draftName(f).trim()}
                    onClick={(e) => {
                      e.stopPropagation();
                      saveFaceName(f);
                    }}
                    {...tip(
                      f.person_id
                        ? "Change this name. If it already belongs to someone in the catalog, this face joins them."
                        : "Give this face a name.",
                    )}
                  >
                    {savingId === f.id ? "Saving…" : savedId === f.id ? "Saved" : "Save name"}
                  </button>
                </div>
              ) : showCrop ? (
                <img src={f.crop_url} alt="" />
              ) : null}
              <div className="face-edit">
                {f.assigned_how === "junk" ? (
                  <>
                    <label className="cluster-label">Hidden as not a person</label>
                    <div className="hint">
                      {faceWhen(f, photo.taken_at) || "no date"} · this face was hidden. Restore it to name them.
                    </div>
                    <div className="row">
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          restorePerson(f.id);
                        }}
                        {...tip("Bring this face back. It is a real person and can be named.")}
                      >
                        This is a person
                      </button>
                    </div>
                  </>
                ) : (
                <>
                <label className="cluster-label" htmlFor={`face-name-${f.id}`}>
                  {f.person_id ? "Name" : unnamedName(f, visibleFaces)}
                </label>
                <input
                  id={`face-name-${f.id}`}
                  value={draftName(f)}
                  placeholder="Type their name"
                  autoComplete="off"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-autocomplete="list"
                  aria-expanded={catalogHits(f, draftName(f)).length > 0}
                  disabled={savingId === f.id}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => {
                    const value = e.target.value;
                    const unique = completeUniqueFirstName(value, people, { excludeId: f.person_id });
                    setDrafts((cur) => ({ ...cur, [f.id]: unique ? unique.name : value }));
                    setNamePick(-1);
                    if (savedId === f.id) setSavedId(null);
                  }}
                  onBlur={(e) => {
                    if (e.relatedTarget?.closest?.(".face-edit, .name-suggest, .person-tiles")) return;
                    const next = draftName(f).trim();
                    if (next && next !== String(f.person_name || "").trim()) saveFaceName(f);
                  }}
                  onKeyDown={(e) => {
                    const typed = Object.prototype.hasOwnProperty.call(drafts, f.id) ? drafts[f.id] : draftName(f);
                    const hits = catalogHits(f, typed);
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
                      const person = (namePick >= 0 && hits[namePick]) || uniqueFirstName(typed, people, { excludeId: f.person_id }) || hits[0];
                      if (person) {
                        e.preventDefault();
                        e.stopPropagation();
                        setDrafts((cur) => ({ ...cur, [f.id]: person.name }));
                        setNamePick(-1);
                      }
                      return;
                    }
                    if (e.key === "Escape" && hits.length) {
                      e.preventDefault();
                      setNamePick(-1);
                      return;
                    }
                    if (e.key === "Enter") {
                      e.preventDefault();
                      e.stopPropagation();
                      const picked = pickCatalogName(f, typed, { useHighlight: namePick >= 0 });
                      if (picked) applyCatalogPerson(picked, f);
                      else saveFaceName(f);
                    }
                  }}
                />
                <NameSuggest
                  query={draftName(f)}
                  people={people}
                  excludeId={f.person_id}
                  activeIndex={f.id === active ? namePick : -1}
                  onPick={(person) => applyCatalogPerson(person, f)}
                />
                <div className="hint">
                  {faceWhen(f, photo.taken_at) || "no date"}
                  {f.quality === "unidentifiable" ? " · unclear" : ""}
                </div>
                <div className="face-note" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
                  {faceNoteOpen === f.id ? (
                    <div className="photo-comment-pop">
                      <label className="cluster-label" htmlFor={`face-note-${f.id}`}>
                        Note about this face
                      </label>
                      <textarea
                        id={`face-note-${f.id}`}
                        rows={3}
                        autoFocus
                        maxLength={4000}
                        value={faceNote(f)}
                        placeholder="A note about this person in this photo. It stays in the catalog, not on the file."
                        onChange={(e) => {
                          const value = e.target.value;
                          setFaceNotes((cur) => ({ ...cur, [f.id]: value }));
                        }}
                        onBlur={() => {
                          saveFaceComment(f);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === "Escape") {
                            e.preventDefault();
                            e.stopPropagation();
                            setFaceNotes((cur) => {
                              const nextDrafts = { ...cur };
                              delete nextDrafts[f.id];
                              return nextDrafts;
                            });
                            setFaceNoteOpen(null);
                          }
                          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                            e.preventDefault();
                            e.stopPropagation();
                            saveFaceComment(f);
                            setFaceNoteOpen(null);
                          }
                        }}
                        {...tip("Saved in the local catalog and the album sidecar, never written onto the original.")}
                      />
                      <button
                        type="button"
                        className="secondary"
                        disabled={faceNoteSaving === f.id || faceNote(f).trim() === String(f.comment || "").trim()}
                        onClick={() => {
                          saveFaceComment(f);
                          setFaceNoteOpen(null);
                        }}
                        {...tip("Save this note. Shortcut ⌘ Enter.")}
                      >
                        {faceNoteSaving === f.id ? "Saving…" : "Save note"}
                      </button>
                    </div>
                  ) : (
                    <button
                      type="button"
                      className={`photo-comment-chip${f.comment ? " has-note" : ""}`}
                      onClick={() => setFaceNoteOpen(f.id)}
                      {...tip("A note about this person in this photo. Saved in the catalog, never written onto the original.")}
                    >
                      {f.comment || "Add a note"}
                    </button>
                  )}
                </div>
                {!f.person_id ? (
                  <div className="person-chips cluster-cats" role="group" aria-label="Family, work, or other">
                    {FACE_CATEGORIES.map((c) => (
                      <button
                        type="button"
                        key={c.id}
                        className={`person-chip ${(categories[f.id] || "") === c.id ? "active" : ""}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setCategories((cur) => ({
                            ...cur,
                            [f.id]: cur[f.id] === c.id ? "" : c.id,
                          }));
                        }}
                        {...tip("Sort this person as family, work, or other in Faces in DB View.")}
                      >
                        {c.label}
                      </button>
                    ))}
                  </div>
                ) : null}
                {f.assigned_how !== "junk" && !showCrop ? (
                  <button
                    type="button"
                    className={`face-save${savedId === f.id ? " saved" : ""}`}
                    disabled={savingId === f.id || !draftName(f).trim()}
                    onClick={(e) => {
                      e.stopPropagation();
                      saveFaceName(f);
                    }}
                    {...tip(
                      f.person_id
                        ? "Change this name. If it already belongs to someone in the catalog, this face joins them."
                        : "Give this face a name.",
                    )}
                  >
                    {savingId === f.id ? "Saving…" : savedId === f.id ? "Saved" : "Save name"}
                  </button>
                ) : null}
                <div className="row">
                  {f.person_id ? (
                    <button
                      type="button"
                      className="secondary"
                      onClick={(e) => {
                        e.stopPropagation();
                        removeName(f.id);
                      }}
                      {...tip("Take this name off the face. The matcher will not put it back. The photo file is not changed.")}
                    >
                      Remove name
                    </button>
                  ) : null}
                  {f.person_id ? (
                    <button
                      type="button"
                      className="secondary"
                      onClick={(e) => {
                        e.stopPropagation();
                        setActive(f.id);
                        setChangingId((cur) => (cur === f.id ? null : f.id));
                      }}
                      {...tip("Pick a different person already in the catalog for this face.")}
                    >
                      {changingId === f.id ? "Cancel" : "Someone else"}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="secondary"
                    onClick={(e) => {
                      e.stopPropagation();
                      markNotPerson(f.id);
                    }}
                    {...tip("This is a statue, painting, or other object. It will stay hidden, and similar faces will be ignored too.")}
                  >
                    Not a person
                  </button>
                </div>
                {!f.person_id && (f.suggestions || []).length ? (
                  <div className="suggest" onClick={(e) => e.stopPropagation()}>
                    {(f.suggestions || []).slice(0, 3).map((s) => (
                      <button
                        key={s.person_id}
                        type="button"
                        className="secondary"
                        onClick={() => assign({ person_id: s.person_id }, f.id)}
                        {...tip(`Put the name ${s.name} on this face.`)}
                      >
                        {s.name}
                      </button>
                    ))}
                  </div>
                ) : null}
                {f.person_id && changingId === f.id ? (
                  <div className="face-name-tools" onClick={(e) => e.stopPropagation()}>
                    <div className="cluster-label">Click the right person</div>
                    <PersonPicker
                      people={people.filter((p) => String(p.id) !== String(f.person_id))}
                      hint="Move this face to that person. The old name stays on their other photos."
                      onPick={(p) => assign({ person_id: p.id }, f.id)}
                    />
                  </div>
                ) : null}
                {f.id === (face?.id || active) && !f.person_id ? (
                  <div className="face-name-tools" onClick={(e) => e.stopPropagation()}>
                    <div className="cluster-label">Or click a name</div>
                    <PersonPicker
                      people={people}
                      hint="Click a face to put that name on this person."
                      onPick={(p) => assign({ person_id: p.id }, f.id)}
                    />
                    <button
                      type="button"
                      className="secondary"
                      onClick={() =>
                        api.unknownFace(f.id).then(async () => {
                          const next = await load();
                          if (next) emitPhotoChange(next);
                          emitCatalogChange();
                        })
                      }
                      {...tip("This is a real person, but you do not know the name yet.")}
                    >
                      Unknown name of person
                    </button>
                    <FamousLookup
                      faceId={f.id}
                      onName={(suggested) => {
                        if (suggested === "") {
                          setDrafts((cur) => ({ ...cur, [f.id]: "" }));
                          return;
                        }
                        setDrafts((cur) => ({
                          ...cur,
                          [f.id]: String(cur[f.id] ?? "").trim() ? cur[f.id] : suggested,
                        }));
                      }}
                      onApplyExisting={(personId) => assign({ person_id: personId }, f.id)}
                      onConfirm={(hit) => assign({ name: hit.name }, f.id)}
                    />
                    {err ? (
                      <p className="error save-error" role="alert">
                        {err}
                      </p>
                    ) : null}
                  </div>
                ) : null}
                </>
                )}
              </div>
            </div>
            );
          })}
        </aside>
      </div>
      {full ? (
        <div
          className={`photo-full ${zoomClass}${pickingFace ? " selecting" : ""}`}
          ref={fullRef}
          onContextMenu={(e) => showPhotoMenu(e, photo)}
          onPointerDownCapture={() => {
            if (pickingNow.current) skipFullClick.current = true;
          }}
          onClick={(e) => {
            if (skipFullClick.current) {
              skipFullClick.current = false;
              return;
            }
            if (pickingNow.current) return;
            if (e.target.closest(".photo-full-tools, .photo-full-nav, .photo-full-east, .photo-full-zoombar, .photo-full-dock, .photo-full-caption, .back-btn, .app-brand, .nav, .photo-full-comment, .photo-sharpen-badge, .photo-imagine-pop, .photo-tag-row, .photo-full-tags")) return;
            if (e.target.closest(".nametag, .face-box")) return;
            if (didDrag.current) return;
            if (clickResetsZoom()) return;
            if (readPlay()) {
              togglePlay();
              return;
            }
            endPlay();
          }}
          onPointerDown={(e) => {
            if (pickingNow.current) skipFullClick.current = true;
            onPanStart(e);
          }}
          onPointerMove={onPanMove}
          onPointerUp={onPanEnd}
          onPointerCancel={onPanEnd}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-label={`${photo.filename} fullscreen`}
        >
          <div
            className="photo-full-zoom"
            style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
          >
            {fullLabels || pickingFace ? (
              <LabeledPhoto
                photo={taggedPhoto}
                src={liveSrc}
                fallbackSrc={photo.thumb_url}
                priority
                activeId={active}
                onFaceClick={(f) => {
                  if (didDrag.current) return;
                  openFace(f);
                }}
                overlayTags={fullLabels}
                showUnnamed
                movable
                onTagMove={moveTag}
                selecting={pickingFace}
                selectingBusy={pickingBusy}
                onRegionSelect={addMissedFace}
                fit
              />
            ) : (
              <img
                src={liveSrc}
                alt={photo.filename}
                fetchPriority="high"
                decoding="async"
                onError={(e) => {
                  if (photo.thumb_url && e.currentTarget.src !== photo.thumb_url) {
                    e.currentTarget.src = photo.thumb_url;
                  }
                }}
              />
            )}
          </div>
          <BackButton
            overlay
            onClick={goBack}
            {...tip("Leave this photo and go back.")}
          />
          {photo.prev_id || play?.ids?.length ? (
            <button
              type="button"
              className="photo-full-nav prev"
              onClick={(e) => {
                e.stopPropagation();
                goPlay(-1);
              }}
              {...tip(play?.ids?.length ? "Previous photo in this play list. Shortcut ←" : "Previous photo in the album. Shortcut ←")}
            >
              Previous
            </button>
          ) : null}
          {photo.next_id || play?.ids?.length ? (
            <div className="photo-full-east" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              className="photo-full-nav next"
              onClick={(e) => {
                e.stopPropagation();
                goPlay(1);
              }}
              {...tip(play?.ids?.length ? "Next photo in this play list. Shortcut →" : "Next photo in the album. Shortcut →")}
            >
              Next
            </button>
            </div>
          ) : null}
          <div
            ref={zoomBarRef}
            className="photo-full-zoombar"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="zoom-tools" role="group" aria-label="Zoom">
            <button
              type="button"
              disabled={zoom >= ZOOM_MAX}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                nudgeZoom(1);
              }}
              {...tip("Zoom in a step, up to 400%. Shortcut +")}
            >
              +
            </button>
            <button
              type="button"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                cycleZoom();
              }}
              {...tip("Cycle 75% → 100% → 200% → 400%. Shortcut 0 fits the photo at 75%.")}
            >
              {Math.round(zoom * 100)}%
            </button>
            <button
              type="button"
              disabled={zoom <= ZOOM_MIN}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                nudgeZoom(-1);
              }}
              {...tip("Zoom out. Shortcut −")}
            >
              −
            </button>
            <button
              type="button"
              aria-pressed={zoom >= ZOOM_MAX - 0.05}
              disabled={zoom >= ZOOM_MAX}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                zoomTo(ZOOM_MAX);
              }}
              {...tip("Zoom the photo to 400%.")}
            >
              400%
            </button>
            </div>
          </div>
          <div
            ref={toolsRef}
            className={`photo-full-tools${toolsPos ? " moved" : ""}${toolsDragging ? " dragging" : ""}`}
            style={toolsPos ? { left: toolsPos.x, top: toolsPos.y } : undefined}
            onClick={(e) => e.stopPropagation()}
            onPointerDown={onToolsPointerDown}
            onPointerMove={onToolsPointerMove}
            onPointerUp={onToolsPointerUp}
            onPointerCancel={onToolsPointerUp}
          >
            <div className="photo-full-actions">
            <span
              className="photo-full-grab"
              aria-label="Move the options bar. Double-click to put it back at the top."
              onDoubleClick={(e) => {
                e.stopPropagation();
                resetToolsPos();
              }}
              {...tip("Drag to move this bar. Double-click to put it back at the top.")}
            >
              <svg viewBox="0 0 10 16" aria-hidden="true">
                <circle cx="3" cy="3" r="1.25" fill="currentColor" />
                <circle cx="7" cy="3" r="1.25" fill="currentColor" />
                <circle cx="3" cy="8" r="1.25" fill="currentColor" />
                <circle cx="7" cy="8" r="1.25" fill="currentColor" />
                <circle cx="3" cy="13" r="1.25" fill="currentColor" />
                <circle cx="7" cy="13" r="1.25" fill="currentColor" />
              </svg>
            </span>
            <button
              type="button"
              onClick={() => {
                stopPlay();
                setPlay(null);
                setFull(false);
                exitBrowserFullscreen();
                const unnamed = (photo.faces || []).find(
                  (item) => !item.person_id && item.assigned_how !== "junk",
                );
                selectFace((unnamed || face || photo.faces?.[0] || {}).id);
              }}
              {...tip("Leave fullscreen and type names on the faces.")}
            >
              Add names
            </button>
            <NamesToggle className="" />
            <LabelLayoutToggle className="" />
            <button
              type="button"
              aria-pressed={pickingFace}
              onClick={toggleMarkFace}
              {...tip(
                pickingFace
                  ? pickingBusy
                    ? "Stop looking and stay on this photo. Shortcut Esc."
                    : "Cancel. Shortcut Esc."
                  : "Draw a box around a person the detector missed, then name them.",
              )}
            >
              {pickingFace ? "Cancel" : "Add a face"}
            </button>
            <button
              type="button"
              onClick={openComment}
              {...tip(photo.comment ? "Edit the comment on this photo." : "Add a comment about this photo.")}
            >
              {photo.comment ? "Edit comment" : "Comment"}
            </button>
            </div>
          </div>
          <div className="photo-full-dock" onClick={(e) => e.stopPropagation()}>
            <button
              type="button"
              aria-pressed={showSharpened}
              disabled={sharpening || imagining}
              onClick={toggleSharpen}
              {...tip("Grok Imagine makes a sharper preview, with extra clarity on faces. The original file is never overwritten.")}
            >
              {sharpenLabel(true)}
            </button>
            <button
              type="button"
              aria-pressed={showImagined || imagineOpen}
              disabled={imagining || sharpening}
              onClick={toggleImagine}
              {...tip("Describe a change and Grok Imagine makes a preview. The original file is never overwritten.")}
            >
              {imagineLabel(true)}
            </button>
            <button
              type="button"
              disabled={rematching}
              onClick={rematchFaces}
              {...tip("Match unnamed faces to the catalog using the closest named photos, several examples of each person, and people already in this album. Names taken off this photo are tried again. Use Undo if a name is wrong.")}
            >
              {rematching ? "Re-identifying…" : "Re-identify"}
            </button>
            {(photo.faces || []).some((f) => f.person_id && f.assigned_how !== "junk") ? (
              <button
                type="button"
                onClick={removeAllNames}
                {...tip("Take every catalog name off this photo. Re-identify can try those faces again. The original file is unchanged.")}
              >
                Remove names
              </button>
            ) : null}
            {(photo.faces || []).some((f) => !f.person_id && f.assigned_how !== "junk") ? (
              <button
                type="button"
                onClick={removeAllUnnamed}
                {...tip("Hide every unnamed face on this photo as not a person. Named people stay. Undo puts them back. Other photos are not changed. The original file is unchanged.")}
              >
                Hide unnamed
              </button>
            ) : null}
            {undoRematch?.faceIds?.length ? (
              <button
                type="button"
                disabled={undoing || rematching}
                onClick={undoRematchFaces}
                {...tip("Remove the names Re-identify just applied on this photo. Names you typed stay.")}
              >
                {undoing
                  ? "Undoing…"
                  : undoRematch.faceIds.length === 1
                    ? "Undo name"
                    : `Undo ${undoRematch.faceIds.length}`}
              </button>
            ) : null}
            {play?.ids?.length ? (
              <button
                type="button"
                onClick={togglePlay}
                {...tip(play.playing ? "Pause. Shortcut space." : "Play. Shortcut space.")}
              >
                {play.playing ? "Pause" : "Play"}
              </button>
            ) : null}
          </div>
          {play?.ids?.length ? (
            <p className="photo-full-caption">
              {play.title || "Play"}
              {playIndex() >= 0 ? ` · ${playIndex() + 1} of ${play.ids.length}` : ""}
            </p>
          ) : seq ? (
            <p className="photo-full-caption">
              Photo {seq.index} of {seq.count}
              {photo.taken_at ? ` · ${photo.taken_at.slice(0, 10)}` : ""}
            </p>
          ) : null}
          {photo.comment ? <p className="photo-full-comment">{photo.comment}</p> : null}
          {imagineOpen ? (
            <div className="photo-imagine-full" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
              <ImaginePrompt
                id="photo-imagine-full"
                value={imaginePrompt}
                onChange={setImaginePrompt}
                onSubmit={runImagine}
                onClose={() => !imagining && setImagineOpen(false)}
                busy={imagining}
                full
              />
            </div>
          ) : null}
          {showImagined ? (
            <button
              type="button"
              className="photo-sharpen-badge full"
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                openImagine();
              }}
              {...tip("Describe a different change. The original file stays untouched.")}
            >
              Changed with Grok · original unchanged
              {imaginePrompt ? ` · ${imaginePrompt}` : ""}
              {" · Change again"}
            </button>
          ) : showSharpened ? (
            <p className="photo-sharpen-badge full">Temporary Grok preview · original unchanged</p>
          ) : null}
          {pickingFace || pickingBusy ? (
          <p className="photo-full-hint">
            {pickingBusy
              ? "Finding the face in that box… Cancel or Esc to stop"
              : "Draw a box around the face to add · Esc cancels"}
          </p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
