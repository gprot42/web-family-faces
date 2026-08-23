import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { estimateEta } from "../jobEta.js";

function splitMessage(message) {
  const text = (message || "").trim();
  if (!text) return { main: "", extra: "" };
  const sep = text.indexOf(" · ");
  if (sep < 0) return { main: text, extra: "" };
  return { main: text.slice(0, sep), extra: text.slice(sep + 3) };
}

function JobGaugeInner({ job, title, resumeable = true, compact = false, onResumed }) {
  const progress = Math.max(0, Number(job.progress) || 0);
  const total = Math.max(0, Number(job.total) || 0);
  const pct = total ? Math.min(100, Math.round((100 * progress) / total)) : 0;
  const paused = job.status === "paused";
  const canResume =
    resumeable && (job.type === "pipeline" || job.type === "scan");
  const { main, extra } = splitMessage(job.message);
  const counting = /counting photos/i.test(main);
  const checking = /checking /i.test(main);
  const scanning = /scanning /i.test(main);
  const heading =
    (checking ? "Looking for new photos" : title || "Finding known faces") + (paused ? " — paused" : "");
  const label = counting
    ? main || "Counting photos…"
    : checking && total
      ? `Checking ${progress.toLocaleString()} of ${total.toLocaleString()} files`
      : scanning && total
        ? `Looking for faces in ${progress.toLocaleString()} of ${total.toLocaleString()} photos`
        : main || (total ? `${progress.toLocaleString()} of ${total.toLocaleString()}` : "Starting…");
  const detail = extra && extra !== label ? extra : "";
  const r = 42;
  const c = 2 * Math.PI * r;
  const filled = total ? (c * pct) / 100 : 0;
  const samples = useRef({ id: null, points: [] });
  const [eta, setEta] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  useEffect(() => {
    const now = Date.now();
    if (samples.current.id !== job.id) {
      samples.current = { id: job.id, points: [{ t: now, p: progress }] };
    } else {
      const points = samples.current.points;
      const last = points[points.length - 1];
      if (!last || last.p !== progress) {
        points.push({ t: now, p: progress });
        const cutoff = now - 90000;
        const recent = points.filter((pt) => pt.t >= cutoff);
        samples.current.points = (recent.length ? recent : points.slice(-8)).slice(-24);
      }
    }
    const update = () => setEta(estimateEta(job, samples.current.points));
    update();
    const id = setInterval(update, 1000);
    return () => clearInterval(id);
  }, [job, progress]);

  return (
    <div className={`job-gauge${compact ? " compact" : ""}`} role="status" aria-live="polite">
      <svg className="job-gauge-dial" viewBox="0 0 120 120" aria-hidden="true">
        <circle className="job-gauge-track" cx="60" cy="60" r={r} />
        <circle
          className="job-gauge-fill"
          cx="60"
          cy="60"
          r={r}
          transform="rotate(-90 60 60)"
          strokeDasharray={c}
          strokeDashoffset={c - filled}
          strokeLinecap={pct >= 100 ? "butt" : "round"}
        />
        <text className="job-gauge-pct" x="60" y="66" textAnchor="middle">
          {total ? `${pct}%` : "…"}
        </text>
      </svg>
      <div className="job-gauge-copy">
        <strong>{heading}</strong>
        <p className="job-gauge-label">
          {label}
          {detail ? <span className="hint"> · {detail}</span> : null}
          {eta && !paused ? <span className="job-gauge-eta"> · {eta}</span> : null}
        </p>
        {!compact && detail ? <p className="hint">{detail}</p> : null}
        {!compact && eta && !paused ? <p className="job-gauge-eta">{eta}</p> : null}
        <div className="job-gauge-actions">
          {paused ? (
            canResume || job.type === "identify" || job.type === "match" || job.type === "cluster" ? (
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true);
                  setErr("");
                  try {
                    await api.resumeJob();
                    onResumed?.();
                  } catch (ex) {
                    setErr(ex.message || "Could not resume.");
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {busy ? "Resuming…" : job.type === "identify" ? "Continue identifying" : "Resume"}
              </button>
            ) : null
          ) : (
            <button
              type="button"
              className="secondary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setErr("");
                try {
                  await api.pauseJob();
                } catch (ex) {
                  setErr(ex.message || "Could not pause.");
                } finally {
                  setBusy(false);
                }
              }}
            >
              {busy ? "Pausing…" : "Pause"}
            </button>
          )}
        </div>
        {err ? <p className="error">{err}</p> : null}
      </div>
    </div>
  );
}

export default function JobGauge({ job, title, resumeable = true, compact = false, onResumed }) {
  if (!job) return null;
  return (
    <JobGaugeInner
      job={job}
      title={title}
      resumeable={resumeable}
      compact={compact}
      onResumed={onResumed}
    />
  );
}
