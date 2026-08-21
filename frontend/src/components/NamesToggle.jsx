import { useEffect, useState } from "react";
import { applyFullscreenLabels, FULLSCREEN_LABELS_EVENT, readFullscreenLabels } from "../nametag.js";
import { tip } from "../tip.js";

export function usePhotoLabels() {
  const [on, setOn] = useState(() => readFullscreenLabels());
  useEffect(() => {
    function sync() {
      setOn(readFullscreenLabels());
    }
    window.addEventListener(FULLSCREEN_LABELS_EVENT, sync);
    return () => window.removeEventListener(FULLSCREEN_LABELS_EVENT, sync);
  }, []);
  function toggle(next = !on) {
    setOn(applyFullscreenLabels(next));
  }
  return [on, toggle];
}

export default function NamesToggle({ className = "secondary" }) {
  const [on, toggle] = usePhotoLabels();
  return (
    <button
      type="button"
      className={className || undefined}
      aria-pressed={on}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        toggle();
      }}
      {...tip(on ? "Hide name labels on the pictures." : "Show name labels on the pictures.")}
    >
      {on ? "Hide names" : "Show names"}
    </button>
  );
}
