const LIMIT = 40;
const stack = [];
const listeners = new Set();

function emit() {
  listeners.forEach((fn) => fn());
}

export function snapshotFace(face) {
  if (!face) return null;
  return {
    person_id: face.person_id ?? null,
    person_name: face.person_name ?? null,
    assigned_how: face.assigned_how ?? null,
    quality: face.quality || "ok",
    comment: face.comment || "",
    tag_x: face.tag_x ?? null,
    tag_y: face.tag_y ?? null,
  };
}

export function pushUndo(entry) {
  if (!entry) return;
  stack.push({ ...entry, at: Date.now() });
  if (stack.length > LIMIT) stack.shift();
  emit();
}

export function pushFaceUndo(photo, face, label) {
  const before = snapshotFace(face);
  if (!photo?.id || !face?.id || !before) return;
  pushUndo({
    type: "face",
    photoId: Number(photo.id),
    faceId: Number(face.id),
    before,
    label: label || "change",
  });
}

export function peekUndo() {
  return stack.length ? stack[stack.length - 1] : null;
}

export function popUndo() {
  const entry = stack.pop() || null;
  if (entry) emit();
  return entry;
}

export function subscribeUndo(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}
