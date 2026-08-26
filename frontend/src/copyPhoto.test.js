import assert from "node:assert/strict";
import test from "node:test";
import { tagBoundsPad, tagsFromFaces } from "./copyPhoto.js";

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

test("tagsFromFaces places a name above a named face", () => {
  const tags = tagsFromFaces(
    {
      width: 200,
      height: 100,
      faces: [{ id: 1, x1: 20, y1: 40, x2: 60, y2: 80, person_id: 9, person_name: "Ada Cole", assigned_how: "manual" }],
    },
    200,
    100,
  );
  assert.equal(tags.items.length, 1);
  assert.equal(tags.items[0].label, "Ada Cole");
  assert.ok(tags.items[0].n === "1");
  assert.ok(tags.items[0].y < 40);
});
