import { useEffect, useState } from "react";
import {
  LABEL_STYLES,
  LABEL_STYLE_EVENT,
  cycleLabelStyle,
  labelStyleInfo,
  readLabelStyle,
} from "../nametag.js";
import { tip } from "../tip.js";

export default function LabelStyleToggle({ className = "secondary" }) {
  const [style, setStyle] = useState(() => readLabelStyle());
  useEffect(() => {
    function sync() {
      setStyle(readLabelStyle());
    }
    window.addEventListener(LABEL_STYLE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(LABEL_STYLE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const cur = labelStyleInfo(style);
  const i = Math.max(0, LABEL_STYLES.findIndex((item) => item.id === style));
  const next = LABEL_STYLES[(i + 1) % LABEL_STYLES.length];
  return (
    <button
      type="button"
      className={className || undefined}
      aria-label={`Label style ${cur.label}. Click for ${next.label}.`}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setStyle(cycleLabelStyle());
      }}
      {...tip(`${cur.hint} Click for ${next.label}.`)}
    >
      {cur.label}
    </button>
  );
}
