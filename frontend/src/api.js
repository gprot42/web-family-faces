function errorDetail(body, fallback) {
  const detail = body?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    if (typeof detail.message === "string" && detail.message.trim()) return detail.message;
    if (typeof detail.detail === "string" && detail.detail.trim()) return detail.detail;
  }
  if (typeof body?.message === "string" && body.message.trim()) return body.message;
  if (body && typeof body === "object") {
    try {
      return JSON.stringify(body);
    } catch {
      /* ignore */
    }
  }
  return fallback;
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch (ex) {
    throw new Error(ex.message || "Could not reach the app. Is it still running?");
  }
  if (!res.ok) {
    let detail = res.statusText || "Request failed";
    try {
      const body = await res.json();
      detail = errorDetail(body, detail);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export const api = {
  stats: () => request("/api/stats"),
  jobs: () => request("/api/jobs"),
  pauseJob: () => request("/api/jobs/pause", { method: "POST" }),
  resumeJob: () => request("/api/jobs/resume", { method: "POST" }),
  health: () => request("/api/health"),
  reportError: (message, extra = {}) =>
    request("/api/log", {
      method: "POST",
      body: JSON.stringify({
        message: String(message || "Unknown error").slice(0, 2000),
        page: extra.page || "",
        action: extra.action || "",
        cluster_id: extra.cluster_id || extra.clusterId || null,
        photo_id: extra.photo_id || extra.photoId || null,
      }),
    }).catch(() => ({ ok: false })),
  pipeline: (folder) => {
    const folders = Array.isArray(folder) ? folder.filter(Boolean) : [folder].filter(Boolean);
    const body = folders.length > 1 ? { folders } : { folder: folders[0] };
    return request("/api/pipeline", { method: "POST", body: JSON.stringify(body) });
  },
  importFolder: (folder) => {
    const folders = Array.isArray(folder) ? folder.filter(Boolean) : [folder].filter(Boolean);
    const body = folders.length > 1 ? { folders } : { folder: folders[0] };
    return request("/api/library/import", { method: "POST", body: JSON.stringify(body) });
  },
  scan: () => request("/api/scan", { method: "POST" }),
  cluster: () => request("/api/cluster", { method: "POST" }),
  match: () => request("/api/match", { method: "POST" }),
  identify: () => request("/api/identify", { method: "POST" }),
  photos: (params = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "" || v === false) continue;
      if (Array.isArray(v)) {
        for (const item of v) {
          if (item !== undefined && item !== null && item !== "") q.append(k, item);
        }
      } else {
        q.set(k, v);
      }
    }
    return request(`/api/photos?${q}`);
  },
  photoTags: () => request("/api/photos/tags"),
  patchPhoto: (id, body) =>
    request(`/api/photos/${id}`, { method: "PATCH", body: JSON.stringify(body || {}) }),
  sharpenPhoto: (id, opts = {}) =>
    request(`/api/photos/${id}/sharpen`, {
      method: "POST",
      body: JSON.stringify({ fresh: Boolean(opts.fresh) }),
    }),
  dropSharpenedPhoto: (id) => request(`/api/photos/${id}/sharpened`, { method: "DELETE" }),
  imagineStatus: (id) => request(`/api/photos/${id}/imagine`),
  imaginePhoto: (id, prompt, opts = {}) =>
    request(`/api/photos/${id}/imagine`, {
      method: "POST",
      body: JSON.stringify({ prompt, fresh: Boolean(opts.fresh) }),
    }),
  dropImaginedPhoto: (id) => request(`/api/photos/${id}/imagined`, { method: "DELETE" }),
  matchPhoto: (id, opts = {}) =>
    request(`/api/photos/${id}/match${opts.wait ? "?wait=1" : ""}`, { method: "POST" }),
  matchPhotoStatus: (id) => request(`/api/photos/${id}/match`),
  waitMatchPhoto: async (id, opts = {}) => {
    let start = opts.start;
    if (!start || start.status === "idle") {
      start = await request(`/api/photos/${id}/match`, { method: "POST", signal: opts.signal });
    }
    if (start.status === "done" || (!start?.started && start?.auto_assigned != null)) return start;
    if (start.status === "error") throw new Error(start.error || "Re-identify failed.");
    const timeout = opts.timeout == null ? 0 : Number(opts.timeout);
    const interval = Number(opts.interval) || 400;
    const t0 = Date.now();
    while (timeout <= 0 || Date.now() - t0 < timeout) {
      if (opts.signal?.aborted) {
        const err = new Error("cancelled");
        err.name = "AbortError";
        throw err;
      }
      await new Promise((resolve) => window.setTimeout(resolve, interval));
      const st = await request(`/api/photos/${id}/match`, { signal: opts.signal });
      if (st.status === "done") return st;
      if (st.status === "error") throw new Error(st.error || "Re-identify failed.");
    }
    throw new Error("Re-identify is still running in the background. You can keep browsing.");
  },
  undoMatchPhoto: (id, faceIds) =>
    request(`/api/photos/${id}/match/undo`, {
      method: "POST",
      body: JSON.stringify({ face_ids: (faceIds || []).map(Number).filter((n) => n > 0) }),
    }),
  warmupFaces: () => request("/api/faces/warmup", { method: "POST" }),
  addPhotoFace: (id, box) =>
    request(`/api/photos/${id}/faces`, { method: "POST", body: JSON.stringify(box) }),
  photo: (id, params = {}) => {
    const cleaned = {};
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      cleaned[k] = v;
    }
    const q = new URLSearchParams(cleaned);
    const qs = q.toString();
    return request(`/api/photos/${id}${qs ? `?${qs}` : ""}`);
  },
  patchFace: (id, body) => request(`/api/faces/${id}`, { method: "PATCH", body: JSON.stringify(body || {}) }),
  assignFace: (id, body) => request(`/api/faces/${id}/assign`, { method: "POST", body: JSON.stringify(body) }),
  unassignFace: (id) => request(`/api/faces/${id}/unassign`, { method: "POST" }),
  unassignPhoto: (id) => request(`/api/photos/${id}/unassign`, { method: "POST" }),
  junkFace: (id) => request(`/api/faces/${id}/junk`, { method: "POST" }),
  restoreFace: (id) => request(`/api/faces/${id}/restore`, { method: "POST" }),
  unknownFace: (id) => request(`/api/faces/${id}/unknown`, { method: "POST" }),
  lookupFace: (id, body = {}) =>
    request(`/api/faces/${id}/lookup`, { method: "POST", body: JSON.stringify(body || {}) }),
  clusters: () => request("/api/clusters"),
  nameCluster: (id, name, face_ids, category) =>
    request(`/api/clusters/${id}/name`, {
      method: "POST",
      body: JSON.stringify({ name, face_ids: face_ids || [], category: category || "" }),
    }),
  assignCluster: (id, person_id, face_ids, category) =>
    request(`/api/clusters/${id}/assign`, {
      method: "POST",
      body: JSON.stringify({ person_id, face_ids: face_ids || [], category: category || "" }),
    }),
  junkCluster: (id, face_ids) =>
    request(`/api/clusters/${id}/junk`, {
      method: "POST",
      body: JSON.stringify({ face_ids: face_ids || [] }),
    }),
  unknownCluster: (id, category, face_ids) =>
    request(`/api/clusters/${id}/unknown`, {
      method: "POST",
      body: JSON.stringify({ category: category || "", face_ids: face_ids || [] }),
    }),
  splitCluster: (id, face_ids) =>
    request(`/api/clusters/${id}/split`, { method: "POST", body: JSON.stringify({ face_ids }) }),
  lookupCluster: (id, body = {}) =>
    request(`/api/clusters/${id}/lookup`, { method: "POST", body: JSON.stringify(body || {}) }),
  people: (folder, opts = {}) => {
    const q = new URLSearchParams();
    if (folder) q.set("folder", folder);
    if (opts.lite) q.set("lite", "1");
    if (opts.names) q.set("names", "1");
    const qs = q.toString();
    return request(`/api/people${qs ? `?${qs}` : ""}`);
  },
  peopleFolders: () => request("/api/people/folders"),
  faceSuggestions: (id) => request(`/api/faces/${id}/suggestions`),
  person: (id) => request(`/api/people/${id}`),
  createPerson: (name) => request("/api/people", { method: "POST", body: JSON.stringify({ name }) }),
  patchPerson: (id, body) => request(`/api/people/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  lookupPerson: (id, body = {}) =>
    request(`/api/people/${id}/lookup`, { method: "POST", body: JSON.stringify(body || {}) }),
  mergePerson: (targetId, sourceId) =>
    request(`/api/people/${targetId}/merge`, { method: "POST", body: JSON.stringify({ source_person_id: sourceId }) }),

  mergeSuggestions: () => request("/api/people/merge-suggestions"),
  search: (q, by = "name") => {
    const params = new URLSearchParams({ q: q || "", by: by === "photo" ? "photo" : "name" });
    return request(`/api/search?${params}`);
  },
  gedcom: () => request("/api/gedcom"),
  gedcomPerson: (id) => request(`/api/gedcom/people/${encodeURIComponent(id)}`),
  uploadGedcom: async (file) => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/gedcom", { method: "POST", body, cache: "no-store" });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json();
        detail = payload.detail || JSON.stringify(payload);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json();
  },
  clearGedcom: () => request("/api/gedcom", { method: "DELETE" }),
  searchFace: async (file) => {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/search/face", { method: "POST", body, cache: "no-store" });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const payload = await res.json();
        detail = payload.detail || JSON.stringify(payload);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json();
  },
  reviewAuto: (params = {}) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v === undefined || v === null || v === "") continue;
      q.set(k, v);
    }
    const suffix = q.toString() ? `?${q}` : "";
    return request(`/api/review/auto${suffix}`);
  },
  confirmAuto: (body) => request("/api/review/auto/confirm", { method: "POST", body: JSON.stringify(body || {}) }),
  confirmFace: (id) => request(`/api/faces/${id}/confirm`, { method: "POST" }),
  browse: (path) => {
    const q = path ? `?path=${encodeURIComponent(path)}` : "";
    return request(`/api/browse${q}`);
  },
  nas: () => request("/api/nas"),
  mountNas: (share, opts = {}) =>
    request("/api/nas/mount", {
      method: "POST",
      body: JSON.stringify({
        ...(share ? { share } : {}),
        all_shares: !!opts.allShares,
      }),
    }),
  resume: () => request("/api/resume"),
  verify: () => request("/api/verify", { method: "POST" }),
  backup: () => request("/api/backup", { method: "POST" }),
  nameFolders: (under) => {
    const q = new URLSearchParams();
    const folders = Array.isArray(under) ? under.filter(Boolean) : under ? [under] : [];
    for (const folder of folders) q.append("under", folder);
    const qs = q.toString();
    return request(`/api/catalog/folders${qs ? `?${qs}` : ""}`);
  },
  resetMatching: (folder) => {
    const folders = Array.isArray(folder) ? folder.filter(Boolean) : folder ? [folder] : [];
    const body = !folders.length ? {} : folders.length === 1 ? { folder: folders[0] } : { folders };
    return request("/api/catalog/reset-matching", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  resetNames: (folder) => {
    const folders = Array.isArray(folder) ? folder.filter(Boolean) : folder ? [folder] : [];
    const body = !folders.length ? {} : folders.length === 1 ? { folder: folders[0] } : { folders };
    return request("/api/catalog/reset", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  settings: (reveal = false) => request(`/api/settings${reveal ? "?reveal=1" : ""}`),
  saveSettings: (body) => request("/api/settings", { method: "PUT", body: JSON.stringify(body) }),
  oauthStart: () => request("/api/settings/oauth/start", { method: "POST" }),
  oauthPoll: () => request("/api/settings/oauth/poll", { method: "POST" }),
  oauthCancel: () => request("/api/settings/oauth/cancel", { method: "POST" }),
  oauthOpen: (browser) =>
    request("/api/settings/oauth/open", { method: "POST", body: JSON.stringify({ browser }) }),
  oauthSignOut: () => request("/api/settings/oauth/sign-out", { method: "POST" }),
};
