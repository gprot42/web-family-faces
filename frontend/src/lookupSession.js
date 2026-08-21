const STORAGE = "photosort-lookups";
const sessions = new Map();
const listeners = new Set();

function persist() {
  const dump = {};
  for (const [key, session] of sessions) {
    dump[key] = { status: session.status, result: session.result, error: session.error };
  }
  try {
    sessionStorage.setItem(STORAGE, JSON.stringify(dump));
  } catch {
    /* private mode */
  }
}

function restore() {
  try {
    const dump = JSON.parse(sessionStorage.getItem(STORAGE) || "{}");
    for (const [key, session] of Object.entries(dump)) {
      if (!session || typeof session !== "object") continue;
      sessions.set(key, {
        status: session.status || "idle",
        result: session.result || null,
        error: session.error || "",
        promise: null,
      });
    }
  } catch {
    /* ignore */
  }
}

restore();

export function lookupKey({ clusterId, faceId, personId } = {}) {
  if (clusterId != null) return `cluster:${clusterId}`;
  if (faceId != null) return `face:${faceId}`;
  if (personId != null) return `person:${personId}`;
  return "";
}

export function subscribeLookups(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  persist();
  for (const fn of listeners) fn();
}

export function getLookupSession(key) {
  return sessions.get(key) || { status: "idle", result: null, error: "" };
}

export function startLookupSession(key, runner) {
  if (!key) return Promise.reject(new Error("Nothing to look up."));
  const current = sessions.get(key);
  if (current?.status === "loading" && current.promise) return current.promise;
  const session = { status: "loading", result: null, error: "", promise: null };
  session.promise = Promise.resolve()
    .then(runner)
    .then((result) => {
      session.status = "done";
      session.result = result;
      session.error = "";
      emit();
      return result;
    })
    .catch((err) => {
      session.status = "error";
      session.error = err?.message || String(err);
      emit();
    });
  sessions.set(key, session);
  emit();
  return session.promise;
}
