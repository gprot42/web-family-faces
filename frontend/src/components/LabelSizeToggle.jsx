import { useEffect, useState } from "react";
import {
  LABEL_SIZES,
  LABEL_SIZE_EVENT,
  cycleLabelSize,
  labelSizeInfo,
  readLabelSize,
} from "../nametag.js";
import { tip } from "../tip.js";

export default function LabelSizeToggle({ className = "secondary" }) {
  const [size, setSize] = useState(() => readLabelSize());
  useEffect(() => {
    function sync() {
      setSize(readLabelSize());
    }
    window.addEventListener(LABEL_SIZE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(LABEL_SIZE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  const cur = labelSizeInfo(size);
  const i = Math.max(0, LABEL_SIZES.findIndex((item) => item.id === size));
  const next = LABEL_SIZES[(i + 1) % LABEL_SIZES.length];
  return (
    <button
      type="button"
      className={className || undefined}
      aria-label={`Label size ${cur.label}. Click for ${next.label}.`}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setSize(cycleLabelSize());
      }}
      {...tip(`${cur.hint} Click for ${next.label}.`)}
    >
      {cur.label}
    </button>
  );
}
