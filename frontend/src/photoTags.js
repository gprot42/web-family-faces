export const TAG_MAX = 40;
export const TAGS_MAX = 12;
export const LATER_REVIEW_TAG = "later review";

export function normalizeTag(value) {
  return String(value || "")
    .trim()
    .replace(/\s+/g, " ")
    .slice(0, TAG_MAX)
    .trim();
}

export function isLaterReviewTag(tag) {
  return normalizeTag(tag).toLowerCase() === LATER_REVIEW_TAG;
}

export function hasLaterReviewTag(tags) {
  return (tags || []).some((tag) => isLaterReviewTag(tag));
}

export function withLaterReviewTag(tags, on) {
  const items = (tags || []).filter(Boolean).filter((tag) => !isLaterReviewTag(tag));
  if (on) {
    if (items.length >= TAGS_MAX) return items.slice(0, TAGS_MAX - 1).concat(LATER_REVIEW_TAG);
    return [...items, LATER_REVIEW_TAG];
  }
  return items;
}

export function otherTags(tags) {
  return (tags || []).filter((tag) => tag && !isLaterReviewTag(tag));
}

export function tagHref(tag) {
  return `/photos?by=tag&tag=${encodeURIComponent(tag)}`;
}

export function photoTagHref(photoId, tag) {
  return `/photos/${photoId}?tag=${encodeURIComponent(tag)}`;
}

export function laterReviewHref() {
  return "/photos?by=later";
}

export function photoLaterHref(photoId) {
  return `/photos/${photoId}?later=1`;
}
