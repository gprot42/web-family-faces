import { useEffect, useState } from "react";
import {
  LABEL_LAYOUTS,
  LABEL_LAYOUT_EVENT,
  cycleLabelLayout,
  labelLayoutInfo,
  readLabelLayout,
} from "../nametag.js";
import { tip } from "../tip.js";

export default function LabelLayoutToggle({ className = "secondary" }) {
  const [layout, setLayout] = useState(() => readLabelLayout());
  useEffect(() => {
    function sync() {
      setLayout(readLabelLayout());
    }
    window.addEventListener(LABEL_LAYOUT_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(LABEL_LAYOUT_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const cur = labelLayoutInfo(layout);
  const i = Math.max(0, LABEL_LAYOUTS.findIndex((item) => item.id === layout));
  const next = LABEL_LAYOUTS[(i + 1) % LABEL_LAYOUTS.length];
  return (
    <button
      type="button"
      className={className || undefined}
      aria-label={`Label layout ${cur.label}. Click for ${next.label}.`}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setLayout(cycleLabelLayout());
      }}
      {...tip(`${cur.hint} Click to try ${next.label}. Names you dragged stay until you pick another layout.`)}
    >
      {cur.label}
    </button>
  );
}
