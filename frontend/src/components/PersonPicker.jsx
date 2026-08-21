import { useMemo, useState } from "react";
import { tip } from "../tip.js";

const CATEGORIES = [
  { id: "family", label: "Family" },
  { id: "work", label: "Work" },
  { id: "other", label: "Other" },
];

export default function PersonPicker({
  people,
  onPick,
  label,
  hint,
  disabled,
  showCategoryFilter,
  categoryFilter,
  onCategoryFilter,
}) {
  const [q, setQ] = useState("");
  const [localCats, setLocalCats] = useState([]);
  const selected = categoryFilter || localCats;
  const setSelected = onCategoryFilter || setLocalCats;
  const list = useMemo(
    () => (people || []).filter((p) => !p.unknown_name),
    [people],
  );
  const byCategory = useMemo(() => {
    if (!selected.length) return list;
    return list.filter((p) => selected.includes(p.category || ""));
  }, [list, selected]);
  const matches = useMemo(() => {
    const parts = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!parts.length) return byCategory;
    return byCategory.filter((p) => {
      const name = (p.name || "").toLowerCase();
      return parts.every((part) => name.includes(part));
    });
  }, [byCategory, q]);

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
        {matches.map((p) => (
          <button
            key={p.id}
            type="button"
            className="person-tile"
            role="listitem"
            disabled={disabled}
            aria-label={p.name}
            onClick={() => onPick(p)}
            {...tip(hint || `This group is ${p.name}.`)}
          >
            {p.cover_url ? (
              <img src={p.cover_url} alt="" decoding="async" />
            ) : (
              <span className="person-picker-gap" />
            )}
            <span className="person-tile-name">{p.name}</span>
          </button>
        ))}
      </div>
      </div>
      {q.trim() && matches.length === 0 ? <p className="hint">No name matches “{q.trim()}”.</p> : null}
      {!q.trim() && selected.length && matches.length === 0 ? (
        <p className="hint">No one marked {selected.map((id) => CATEGORIES.find((c) => c.id === id)?.label).filter(Boolean).join(" or ")} yet.</p>
      ) : null}
    </div>
  );
}
