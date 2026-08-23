import { Fragment, useMemo, useState } from "react";
import { tip } from "../tip.js";

export const PERSON_DRAG_TYPE = "application/x-photosort-person";

const CATEGORIES = [
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

export function personFromDataTransfer(dt) {
  if (!dt) return null;
  try {
    const raw = dt.getData(PERSON_DRAG_TYPE) || dt.getData("application/json");
    if (raw) {
      const data = JSON.parse(raw);
      if (data && (data.id != null || data.person_id != null)) {
        return { id: data.id ?? data.person_id, name: data.name || "" };
      }
    }
  } catch {
    /* ignore */
  }
  const text = String(dt.getData("text/plain") || "");
  const match = text.match(/^person:(\d+):(.*)$/);
  if (match) return { id: Number(match[1]), name: match[2] };
  return null;
}

export function isPersonDrag(dt) {
  const types = [...(dt?.types || [])];
  return types.includes(PERSON_DRAG_TYPE) || types.includes("application/json") || types.includes("text/plain");
}

export default function PersonPicker({
  people,
  onPick,
  label,
  hint,
  disabled,
  showCategoryFilter,
  categoryFilter,
  onCategoryFilter,
  hideable = false,
  hiddenIds = [],
  showHidden = false,
  onHide,
  onUnhide,
  onToggleHidden,
}) {
  const [q, setQ] = useState("");
  const [localCats, setLocalCats] = useState([]);
  const selected = categoryFilter || localCats;
  const setSelected = onCategoryFilter || setLocalCats;
  const hidden = useMemo(() => new Set((hiddenIds || []).map(Number)), [hiddenIds]);
  const list = useMemo(
    () => (people || []).filter((p) => !p.unknown_name),
    [people],
  );
  const byCategory = useMemo(() => {
    if (!selected.length) return list;
    return list.filter((p) => selected.includes(p.category || ""));
  }, [list, selected]);
  const visible = useMemo(() => {
    if (!hideable || showHidden) return byCategory;
    return byCategory.filter((p) => !hidden.has(Number(p.id)));
  }, [byCategory, hideable, showHidden, hidden]);
  const matches = useMemo(() => {
    const parts = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!parts.length) return visible;
    return visible.filter((p) => {
      const name = (p.name || "").toLowerCase();
      return parts.every((part) => name.includes(part));
    });
  }, [visible, q]);
  const hiddenCount = hideable ? byCategory.filter((p) => hidden.has(Number(p.id))).length : 0;

  function toggleCat(id) {
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

  if (!list.length) {
    return (
      <div className="person-picker">
        {label ? <span className="cluster-label">{label}</span> : null}
        <p className="hint">Name someone first, then they appear here as a face you can click.</p>
      </div>
    );
  }

  return (
    <div className="person-picker">
      {label ? <span className="cluster-label">{label}</span> : null}
      {showCategoryFilter ? (
        <div className="person-chips person-picker-cats" role="group" aria-label="Show family, work, or other">
          {CATEGORIES.map((c) => (
            <button
              type="button"
              key={c.id}
              className={`person-chip ${selected.includes(c.id) ? "active" : ""}`}
              aria-pressed={selected.includes(c.id)}
              onClick={() => toggleCat(c.id)}
              {...tip(`Show people marked ${c.label}. You can pick more than one.`)}
            >
              {c.label}
            </button>
          ))}
        </div>
      ) : null}
      {list.length > 8 ? (
        <input
          type="search"
          value={q}
          placeholder="Filter names"
          onChange={(e) => setQ(e.target.value)}
        />
      ) : null}
      <div className="person-tiles-scroll">
      <div className="person-tiles" role="list">
        {matches.map((p) => {
          const isHidden = hidden.has(Number(p.id));
          const tile = (
            <button
              type="button"
              className="person-tile"
              role={hideable ? undefined : "listitem"}
              disabled={disabled}
              aria-label={p.name}
              draggable={!disabled}
              onClick={() => onPick(p)}
              onDragStart={(e) => {
                const payload = JSON.stringify({ id: p.id, name: p.name });
                e.dataTransfer.setData(PERSON_DRAG_TYPE, payload);
                e.dataTransfer.setData("application/json", payload);
                e.dataTransfer.setData("text/plain", `person:${p.id}:${p.name}`);
                e.dataTransfer.effectAllowed = "copy";
                const img = e.currentTarget.querySelector("img");
                if (img) e.dataTransfer.setDragImage(img, 24, 24);
              }}
              {...tip(hint || `This group is ${p.name}. Drag onto Name this person, or click.`)}
            >
              {p.cover_url ? (
                <img src={p.cover_url} alt="" decoding="async" />
              ) : (
                <span className="person-picker-gap" />
              )}
              <span className="person-tile-name">{p.name}</span>
            </button>
          );
          if (!hideable) {
            return <Fragment key={p.id}>{tile}</Fragment>;
          }
          return (
            <div
              key={p.id}
              className={`person-tile-wrap${isHidden ? " is-hidden" : ""}`}
              role="listitem"
            >
              {tile}
              <button
                type="button"
                className="person-tile-x"
                disabled={disabled}
                aria-label={isHidden ? `Show ${p.name} in tagging` : `Hide ${p.name} from tagging`}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  if (isHidden) onUnhide?.(p);
                  else onHide?.(p);
                }}
                {...tip(
                  isHidden
                    ? `Show ${p.name} in this tagging list again. They stay in Faces in DB View.`
                    : `Hide ${p.name} from this tagging list. They stay in Faces in DB View.`,
                )}
              >
                {isHidden ? "Show" : "×"}
              </button>
            </div>
          );
        })}
      </div>
      </div>
      {hideable && hiddenCount ? (
        <button
          type="button"
          className="secondary person-picker-hidden"
          onClick={() => onToggleHidden?.()}
          {...tip("People you hid with ×. They are still in Faces in DB View.")}
        >
          {showHidden
            ? "Hide them again"
            : hiddenCount === 1
              ? "1 hidden from tagging"
              : `${hiddenCount} hidden from tagging`}
        </button>
      ) : null}
      {q.trim() && matches.length === 0 ? <p className="hint">No name matches “{q.trim()}”.</p> : null}
      {!q.trim() && selected.length && matches.length === 0 ? (
        <p className="hint">No one marked {selected.map((id) => CATEGORIES.find((c) => c.id === id)?.label).filter(Boolean).join(" or ")} yet.</p>
      ) : null}
      {hideable && !showHidden && hiddenCount && !q.trim() && !selected.length && matches.length === 0 ? (
        <p className="hint">Everyone here is hidden from tagging. Show them below.</p>
      ) : null}
    </div>
  );
}
