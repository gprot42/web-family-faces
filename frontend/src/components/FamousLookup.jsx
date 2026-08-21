import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { getLookupSession, lookupKey, startLookupSession, subscribeLookups } from "../lookupSession.js";
import { tip } from "../tip.js";

export default function FamousLookup({
  clusterId,
  faceId,
  personId,
  faceIds,
  onName,
  onApplyExisting,
  onConfirm,
  variant = "full",
  disabled = false,
  available: availableProp,
}) {
  const [available, setAvailable] = useState(availableProp ?? true);
  const [note, setNote] = useState("");
  const [rejected, setRejected] = useState([]);
  const [, bump] = useState(0);
  const key = lookupKey({ clusterId, faceId, personId });
  const session = getLookupSession(key);
  const loading = session.status === "loading";
  const result = session.status === "done" ? session.result : null;
  const err = session.status === "error" ? session.error : "";

  useEffect(() => {
    if (availableProp != null) {
      setAvailable(Boolean(availableProp));
      return undefined;
    }
    api
      .health()
      .then((h) => setAvailable(Boolean(h.lookup?.available)))
      .catch(() => setAvailable(false));
    return undefined;
  }, [availableProp]);

  useEffect(() => subscribeLookups(() => bump((n) => n + 1)), []);

  useEffect(() => {
    setNote("");
    setRejected([]);
  }, [key]);

  useEffect(() => {
    if (variant === "results") return;
    if (session.status === "loading" && !session.promise) run();
    // Restart a lookup that was in flight when this page remounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, variant]);

  useEffect(() => {
    if (variant === "launch") return;
    if (session.status === "done" && session.result?.found && session.result.name) {
      onName?.(session.result.name);
    }
    // Fill the name field when a background lookup finishes, even if this card remounted.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to this session result
  }, [key, session.status, session.result, variant]);

  function run(opts = {}) {
    const rejectedNames = opts.rejected ?? rejected;
    const userNote = opts.note != null ? opts.note : note;
    const body = {};
    if (String(userNote || "").trim()) body.note = String(userNote).trim();
    if (rejectedNames.length) body.rejected_names = rejectedNames;
    if (faceIds?.length) body.face_ids = faceIds;
    startLookupSession(key, async () => {
      if (clusterId != null) return api.lookupCluster(clusterId, body);
      if (faceId != null) return api.lookupFace(faceId, body);
      if (personId != null) return api.lookupPerson(personId, body);
      throw new Error("Nothing to look up.");
    });
    setNote("");
  }

  function notThisPerson() {
    const guess = (result?.name || "").trim();
    const next = guess && !rejected.includes(guess) ? [...rejected, guess] : rejected;
    setRejected(next);
    onName?.("");
    run({
      rejected: next,
      note: note.trim() || (guess ? `That is not ${guess}. Try a different person.` : "Try again."),
    });
  }

  function apply() {
    if (!result?.found || !result.name) return;
    if (result.existing_person_id && onApplyExisting) onApplyExisting(result.existing_person_id, result);
    else if (onConfirm) onConfirm(result);
    else onName?.(result.name, result);
  }

  const compact = variant === "launch" || variant === "compact";
  const showLaunch = variant !== "results";
  const showBody = variant !== "launch";
  const idleLabel = compact ? "AI" : "Look up famous face";
  if (variant === "results" && !loading && !result && !err) return null;

  return (
    <div className={`famous-lookup${compact ? " compact" : ""}`}>
      {showLaunch ? (
        <button
          type="button"
          className={`secondary lookup-launch${compact ? " cluster-ai" : ""}`}
          disabled={loading || disabled}
          aria-label={loading ? "Looking up this group" : "Look up this group with AI"}
          onClick={run}
          {...tip(
            available
              ? compact
                ? "Look this group up with AI. Only a face crop is sent — not the original photo. You still choose the name."
                : "Send this face crop — not the original photo — plus filename, dates, EXIF, and already-named people. You still choose the name."
              : "Open Settings and paste an xAI key, or sign in with SuperGrok. Only a face crop is sent.",
          )}
        >
          {compact ? (
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M7.2 1.2 8.4 5.2 12.4 6.4 8.4 7.6 7.2 11.6 6 7.6 2 6.4 6 5.2Z"
              />
              <path fill="currentColor" d="M12.2 9.2 12.8 11.3 14.9 11.9 12.8 12.5 12.2 14.6 11.6 12.5 9.5 11.9 11.6 11.3Z" />
            </svg>
          ) : (
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path
                fill="currentColor"
                d="M7.2 1.6a5.6 5.6 0 1 0 3.4 10l2.9 2.9 1.1-1.1-2.9-2.9A5.6 5.6 0 0 0 7.2 1.6Zm0 1.8a3.8 3.8 0 1 1 0 7.6 3.8 3.8 0 0 1 0-7.6Z"
              />
            </svg>
          )}
          {loading ? (compact ? "…" : "Looking up…") : idleLabel}
        </button>
      ) : null}
      {showBody && !available && !loading && variant !== "results" ? (
        <p className="hint">
          <Link to="/settings">Add an xAI key or SuperGrok in Settings</Link> to look up famous faces.
        </p>
      ) : null}
      {showBody && loading ? (
        <p className="lookup-wait" role="status" aria-live="polite">
          <span className="lookup-wait-dot" aria-hidden="true" />
          Checking public photos. This can take a couple of minutes.
        </p>
      ) : null}
      {showBody && result?.found ? (
        <div className="lookup-hit">
          <div>
            <strong>{result.name}</strong>
            <span className="lookup-certainty" title="How sure the lookup is">
              {result.confidence_pct ?? (result.confidence === "high" ? 85 : result.confidence === "medium" ? 60 : 30)}%
              sure
            </span>
            <span className="hint">
              {result.role ? ` · ${result.role}` : ""}
              {result.existing_person_name ? ` · already stored in the database` : ""}
            </span>
            {result.why ? <p className="hint">{result.why}</p> : null}
            {rejected.length ? (
              <p className="hint">Already tried: {rejected.join(", ")}</p>
            ) : null}
          </div>
          <button type="button" onClick={apply} {...tip("Put this name on the face. You can still change it later.")}>
            Use this name
          </button>
        </div>
      ) : null}
      {showBody && result && !result.found ? (
        <div className="lookup-miss">
          <p className="hint">No famous match yet. Type a name, or use Unknown.</p>
          {result.why ? <p className="hint">{result.why}</p> : null}
          {rejected.length ? <p className="hint">Already tried: {rejected.join(", ")}</p> : null}
        </div>
      ) : null}
      {showBody && (result || err) ? (
        <form
          className="lookup-reply"
          onSubmit={(e) => {
            e.preventDefault();
            run({ note: note.trim() || "Try again." });
          }}
        >
          <label className="cluster-label" htmlFor={`lookup-note-${key}`}>
            Reply to the lookup
          </label>
          <textarea
            id={`lookup-note-${key}`}
            className="lookup-note"
            rows={2}
            value={note}
            disabled={loading}
            placeholder="Try again, or say why this is not the right person."
            onChange={(e) => setNote(e.target.value)}
          />
          <div className="row">
            <button
              type="submit"
              className="secondary"
              disabled={loading}
              {...tip("Send your comment and look again. Only the face crop is sent, plus this text.")}
            >
              Send and try again
            </button>
            {result?.found ? (
              <button
                type="button"
                className="secondary"
                disabled={loading}
                onClick={notThisPerson}
                {...tip("Tell the lookup this name is wrong and search for someone else.")}
              >
                Not this person
              </button>
            ) : (
              <button
                type="button"
                className="secondary"
                disabled={loading}
                onClick={() => run({ note: "Try again." })}
                {...tip("Look again without extra comment.")}
              >
                Try again
              </button>
            )}
          </div>
        </form>
      ) : null}
      {showBody && err ? <p className="error">{err}</p> : null}
    </div>
  );
}
