import { Link } from "react-router-dom";
import { tip } from "../tip.js";
import { TAG_MAX, TAGS_MAX, normalizeTag, tagHref } from "../photoTags.js";

export {
  TAG_MAX,
  TAGS_MAX,
  LATER_REVIEW_TAG,
  normalizeTag,
  isLaterReviewTag,
  hasLaterReviewTag,
  withLaterReviewTag,
  otherTags,
  tagHref,
  photoTagHref,
  laterReviewHref,
  photoLaterHref,
} from "../photoTags.js";

export function PhotoTagRow({ tags, link, onRemove, children }) {
  const items = (tags || []).filter(Boolean);
  if (!items.length && !children) return null;
  return (
    <div className="photo-tag-row" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      {items.map((tag) => (
        <span key={tag} className="photo-tag">
          {link ? (
            <Link to={tagHref(tag)} onClick={(e) => e.stopPropagation()} {...tip(`Show photos tagged ${tag}.`)}>
              {tag}
            </Link>
          ) : (
            tag
          )}
          {onRemove ? (
            <button
              type="button"
              className="photo-tag-x"
              aria-label={`Remove tag ${tag}`}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                onRemove(tag);
              }}
            >
              ×
            </button>
          ) : null}
        </span>
      ))}
      {children}
    </div>
  );
}

export default function PhotoTagEditor({ tags, onChange, compact }) {
  const items = tags || [];
  return (
    <PhotoTagRow tags={items} link onRemove={items.length ? (tag) => onChange(items.filter((item) => item !== tag)) : undefined}>
      {items.length < TAGS_MAX ? (
        <input
          className="photo-tag-input"
          type="text"
          maxLength={TAG_MAX}
          placeholder={compact ? "Add tag" : "Add a tag"}
          aria-label="Add a custom tag"
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              e.stopPropagation();
              const next = normalizeTag(e.currentTarget.value);
              if (!next) return;
              if (items.some((item) => item.toLowerCase() === next.toLowerCase())) {
                e.currentTarget.value = "";
                return;
              }
              onChange([...items, next]);
              e.currentTarget.value = "";
            }
            if (e.key === "Escape") {
              e.currentTarget.blur();
            }
          }}
          onBlur={(e) => {
            const next = normalizeTag(e.currentTarget.value);
            if (!next) return;
            if (items.some((item) => item.toLowerCase() === next.toLowerCase())) {
              e.currentTarget.value = "";
              return;
            }
            if (items.length >= TAGS_MAX) return;
            onChange([...items, next]);
            e.currentTarget.value = "";
          }}
          {...tip("Type a tag and press Enter. It stays in the catalog, not on the file.")}
        />
      ) : null}
    </PhotoTagRow>
  );
}
