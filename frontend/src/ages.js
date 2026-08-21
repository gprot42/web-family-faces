export function photoYear(takenAt) {
  if (!takenAt) return null;
  const y = Number(String(takenAt).slice(0, 4));
  return y >= 1800 && y <= 2100 ? y : null;
}

export function ageBand(age) {
  if (age == null || Number.isNaN(Number(age))) return null;
  const n = Number(age);
  if (n < 13) return "child";
  if (n < 21) return "young";
  return "adult";
}

export function faceWhen(face, fallbackTakenAt) {
  const year = photoYear(face.taken_at) || photoYear(fallbackTakenAt);
  if (year) return String(year);
  const band = ageBand(face.age_est);
  return band || "";
}

export function yearRange(faces) {
  const years = (faces || []).map((f) => photoYear(f.taken_at)).filter((y) => y != null);
  if (!years.length) return null;
  const lo = Math.min(...years);
  const hi = Math.max(...years);
  return lo === hi ? `photos from ${lo}` : `photos from ${lo}–${hi}`;
}

export function groupWhen(cluster) {
  const years = yearRange(cluster.faces);
  if (years) return years;
  const band = ageBand(cluster.age_mean);
  return band ? `looks ${band}` : "";
}
