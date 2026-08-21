import { useRef } from "react";
import { tip } from "../tip.js";

export const IMAGINE_PRESETS = [
  "Restore faded colours",
  "Convert to black and white",
  "Colourise this photo",
  "Repair scratches and dust",
];

export default function ImaginePrompt({
  id = "photo-imagine",
  value,
  onChange,
  onSubmit,
  onClose,
  busy,
  full = false,
}) {
  const ref = useRef(null);
  const ready = value.trim().length >= 3 && !busy;

  return (
    <div className={`photo-comment-pop photo-imagine-pop${full ? " full" : ""}`} id={id}>
      <div className="people-search-head">
        <label className="cluster-label" htmlFor={`${id}-prompt`}>
          Change with Grok
        </label>
        <button type="button" className="ghost" onClick={onClose} disabled={busy}>
          Close
        </button>
      </div>
      <textarea
        id={`${id}-prompt`}
        ref={ref}
        rows={4}
        maxLength={2000}
        value={value}
        disabled={busy}
        placeholder="Describe the change. The original file is never overwritten."
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            e.stopPropagation();
            if (!busy) onClose();
          }
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            if (ready) onSubmit();
          }
        }}
        {...tip("Grok Imagine edits a copy for this view. The NAS original stays untouched.")}
      />
      <p className="hint">Temporary preview. The original file is never overwritten.</p>
      <div className="photo-imagine-presets">
        {IMAGINE_PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className="ghost"
            disabled={busy}
            onClick={() => onChange(preset)}
          >
            {preset}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="secondary"
        disabled={!ready}
        onClick={onSubmit}
        {...tip("Send this photo to Grok Imagine. Shortcut ⌘ Enter.")}
      >
        {busy ? "Changing…" : "Change photo"}
      </button>
    </div>
  );
}
