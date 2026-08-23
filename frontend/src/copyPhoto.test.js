import assert from "node:assert/strict";
import test from "node:test";
import { tagBoundsPad } from "./copyPhoto.js";

test("tagBoundsPad is empty when there are no tags", () => {
  assert.deepEqual(tagBoundsPad(800, 600, []), { left: 0, top: 0, right: 0, bottom: 0 });
});

test("tagBoundsPad stays tight when tags sit on the photo", () => {
  assert.deepEqual(
    tagBoundsPad(800, 600, [{ x: 20, y: 40, w: 80, h: 18 }]),
    { left: 0, top: 0, right: 0, bottom: 0 },
  );
});

test("tagBoundsPad adds room for a name below the photo", () => {
  assert.deepEqual(
    tagBoundsPad(800, 600, [{ x: 100, y: 590, w: 120, h: 22 }], 8),
    { left: 0, top: 0, right: 0, bottom: 20 },
  );
});

test("tagBoundsPad adds room for a name above or beside the photo", () => {
  assert.deepEqual(
    tagBoundsPad(800, 600, [{ x: -10, y: -12, w: 90, h: 20 }], 4),
    { left: 14, top: 16, right: 0, bottom: 0 },
  );
});
