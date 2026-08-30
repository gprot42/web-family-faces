import assert from "node:assert/strict";
import test from "node:test";
import {
  hasLaterReviewTag,
  isLaterReviewTag,
  LATER_REVIEW_TAG,
  otherTags,
  TAGS_MAX,
  withLaterReviewTag,
} from "../photoTags.js";

test("later review tag matches ignoring case", () => {
  assert.equal(isLaterReviewTag("Later Review"), true);
  assert.equal(hasLaterReviewTag(["school", LATER_REVIEW_TAG]), true);
  assert.equal(hasLaterReviewTag(["school"]), false);
});

test("withLaterReviewTag adds and removes the reserved tag", () => {
  assert.deepEqual(withLaterReviewTag(["school"], true), ["school", LATER_REVIEW_TAG]);
  assert.deepEqual(withLaterReviewTag(["school", LATER_REVIEW_TAG], false), ["school"]);
  assert.deepEqual(otherTags(["school", LATER_REVIEW_TAG, "2018"]), ["school", "2018"]);
});

test("withLaterReviewTag keeps the reserved tag when the photo is full", () => {
  const full = Array.from({ length: TAGS_MAX }, (_, i) => `t${i}`);
  const next = withLaterReviewTag(full, true);
  assert.equal(next.length, TAGS_MAX);
  assert.equal(hasLaterReviewTag(next), true);
});
