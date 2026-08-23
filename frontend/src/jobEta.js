export function formatEta(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return "";
  if (seconds < 45) return "less than a minute left";
  const minutes = Math.round(seconds / 60);
  if (minutes === 1) return "about 1 minute left";
  if (minutes < 90) return `about ${minutes} minutes left`;
  const hours = seconds / 3600;
  const h = Math.floor(hours);
  const m = Math.round((seconds % 3600) / 60);
  if (h < 1) return `about ${minutes} minutes left`;
  if (h === 1 && m < 8) return "about 1 hour left";
  if (h === 1) return `about 1 hour ${m} min left`;
  if (m < 8) return `about ${h} hours left`;
  return `about ${h} hours ${m} min left`;
}

export function estimateEta(job, samples) {
  const progress = Math.max(0, Number(job?.progress) || 0);
  const total = Math.max(0, Number(job?.total) || 0);
  if (!total || progress >= total) return "";
  const remaining = total - progress;
  if (!Array.isArray(samples) || samples.length < 2) return "";
  const last = samples[samples.length - 1];
  const window = samples.filter(
    (pt) => last.t - pt.t <= 90000 && last.p - pt.p >= 0 && last.p - pt.p <= Math.max(250, total * 0.25),
  );
  const first = window[0];
  if (!first) return "";
  const dp = last.p - first.p;
  const dt = (last.t - first.t) / 1000;
  if (dp < 2 || dt < 15) return "";
  const rate = dp / dt;
  if (!rate) return "";
  let seconds = remaining / rate;
  if (remaining > 80 && seconds < 60) seconds = 60;
  return formatEta(seconds);
}
