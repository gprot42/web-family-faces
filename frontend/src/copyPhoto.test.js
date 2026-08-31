import assert from "node:assert/strict";
import test from "node:test";
import {
  copyChipFontPx,
  fitCopiedTags,
  nameTagChipSize,
  tagBoundsPad,
  tagsFromFaces,
  visualRectToLayout,
} from "./copyPhoto.js";

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

test("tagsFromFaces keeps the same chip height for every name", () => {
  const tags = tagsFromFaces(
    {
      width: 960,
      height: 640,
      faces: [
        { id: 1, x1: 40, y1: 80, x2: 120, y2: 180, person_id: 1, person_name: "Jo", assigned_how: "manual" },
        {
          id: 2,
          x1: 400,
          y1: 80,
          x2: 520,
          y2: 180,
          person_id: 2,
          person_name: "Christopher Wellington",
          assigned_how: "manual",
        },
      ],
    },
    960,
    640,
  );
  assert.equal(tags.items.length, 2);
  assert.equal(tags.items[0].fontSize, tags.items[1].fontSize);
  assert.equal(tags.items[0].h, tags.items[1].h);
  assert.ok(tags.items[1].w > tags.items[0].w);
});

test("fitCopiedTags normalizes mixed on-screen sizes to one named height", () => {
  const fitted = fitCopiedTags({
    imgW: 800,
    imgH: 600,
    items: [
      { x: 10, y: 10, w: 40, h: 12, fontSize: 9, label: "Ada", n: "1", text: "1 Ada" },
      { x: 80, y: 8, w: 160, h: 28, fontSize: 14, label: "Christopher", n: "2", text: "2 Christopher" },
    ],
  });
  assert.equal(fitted.items[0].fontSize, fitted.items[1].fontSize);
  assert.equal(fitted.items[0].h, fitted.items[1].h);
  assert.ok(fitted.items[1].w > fitted.items[0].w);
});

test("nameTagChipSize uses one height for a given font", () => {
  const a = nameTagChipSize({ fontSize: 10, label: "Jo", n: "1" });
  const b = nameTagChipSize({ fontSize: 10, label: "Christopher Wellington", n: "2" });
  assert.equal(a.h, b.h);
  assert.equal(a.fontSize, b.fontSize);
  assert.ok(b.w > a.w);
});

test("copyChipFontPx follows Small / Medium / Large", () => {
  assert.equal(copyChipFontPx(960, "small"), 10);
  assert.equal(copyChipFontPx(960, "medium"), 11);
  assert.equal(copyChipFontPx(960, "large"), 13);
  assert.equal(copyChipFontPx(1920, "small"), 20);
});

test("visualRectToLayout undoes CSS zoom so copy size matches the unzoomed photo", () => {
  const layout = { w: 800, h: 600 };
  const tag = { left: 100, top: 40, width: 72, height: 18 };
  function atZoom(z) {
    const imgRect = { left: 10, top: 20, width: layout.w * z, height: layout.h * z };
    const r = {
      left: imgRect.left + tag.left * z,
      top: imgRect.top + tag.top * z,
      width: tag.width * z,
      height: tag.height * z,
    };
    return visualRectToLayout(r, { rect: imgRect, w: layout.w, h: layout.h, sx: z, sy: z });
  }
  const fit = atZoom(0.75);
  const zoomed = atZoom(2);
  assert.equal(Math.round(fit.w), tag.width);
  assert.equal(Math.round(fit.h), tag.height);
  assert.deepEqual(
    { x: Math.round(fit.x), y: Math.round(fit.y), w: Math.round(fit.w), h: Math.round(fit.h) },
    { x: Math.round(zoomed.x), y: Math.round(zoomed.y), w: Math.round(zoomed.w), h: Math.round(zoomed.h) },
  );
});

test("tag place on the photo is the same fraction at 75% and 200% zoom", () => {
  function frac(z) {
    const img = { left: 40, top: 20, width: 800 * z, height: 534 * z };
    const tag = { left: img.left + 124 * z, top: img.top + 80 * z, width: 70 * z, height: 16 * z };
    return {
      x: (tag.left - img.left) / img.width,
      y: (tag.top - img.top) / img.height,
    };
  }
  assert.deepEqual(frac(0.75), frac(2));
  assert.equal(frac(1).x.toFixed(4), (124 / 800).toFixed(4));
});
